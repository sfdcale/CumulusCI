import abc
import itertools
import logging
import os
from typing import List, Optional

import pydantic
from github3.exceptions import NotFoundError
from github3.repos.repo import Repository
from pydantic.networks import AnyUrl

from cumulusci.core.config import OrgConfig
from cumulusci.core.config.project_config import BaseProjectConfig
from cumulusci.core.dependencies.github import (
    get_package_data,
    get_remote_project_config,
    get_repo,
)
from cumulusci.core.dependencies.utils import TaskContext
from cumulusci.core.exceptions import (
    DependencyParseError,
    DependencyResolutionError,
)
from cumulusci.salesforce_api.metadata import ApiDeploy
from cumulusci.salesforce_api.package_install import (
    DEFAULT_PACKAGE_RETRY_OPTIONS,
    PackageInstallOptions,
    install_package_by_namespace_version,
    install_package_by_version_id,
)
from cumulusci.salesforce_api.package_zip import MetadataPackageZipBuilder
from cumulusci.utils import download_extract_github_from_repo, download_extract_zip
from cumulusci.utils.git import split_repo_url
from cumulusci.utils.yaml.model_parser import CCIModel

logger = logging.getLogger(__name__)


def _validate_github_parameters(values):
    if values.get("repo_owner") or values.get("repo_name"):
        logger.warning(
            "The repo_name and repo_owner keys are deprecated. Please use the github key."
        )

    assert values.get("github") or (
        values.get("repo_owner") and values.get("repo_name")
    ), "Must specify `github` or `repo_owner` and `repo_name`"

    # Populate the `github` property if not already populated.
    if not values.get("github") and values.get("repo_name"):
        values[
            "github"
        ] = f"https://github.com/{values['repo_owner']}/{values['repo_name']}"
        values.pop("repo_owner")
        values.pop("repo_name")

    return values


class HashableBaseModel(CCIModel):
    """Base Pydantic model class that has a functional `hash()` method.
    Requires that model can be converted to JSON."""

    # See https://github.com/samuelcolvin/pydantic/issues/1303
    def __hash__(self):
        return hash((type(self),) + tuple(self.json()))


class Dependency(HashableBaseModel, abc.ABC):
    """Abstract base class for models representing dependencies

    Dependencies can be _resolved_ to an immutable version, or not.
    They can also be _flattened_ (turned into a list including their own transitive dependencies) or not.
    """

    @property
    @abc.abstractmethod
    def name(self):
        pass

    @property
    def description(self):
        return self.name

    @property
    @abc.abstractmethod
    def is_resolved(self):
        return False

    @property
    @abc.abstractmethod
    def is_flattened(self):
        return False

    def flatten(self, context: BaseProjectConfig) -> List["Dependency"]:
        """Get a list including this dependency as well as its transitive dependencies."""
        return [self]

    def __str__(self):
        return self.description


Dependency.update_forward_refs()


class StaticDependency(Dependency, abc.ABC):
    """Abstract base class for dependencies that we know how to install (i.e., they
    are already both resolved and flattened)."""

    @abc.abstractmethod
    def install(self, org_config: OrgConfig, retry_options: dict = None):
        pass

    @property
    def is_resolved(self):
        return True

    @property
    def is_flattened(self):
        return True


class DynamicDependency(Dependency, abc.ABC):
    """Abstract base class for dependencies with dynamic references, like GitHub.
    These dependencies must be resolved and flattened before they can be installed."""

    managed_dependency: Optional[StaticDependency]
    password_env_name: Optional[str]

    @property
    def is_flattened(self):
        return False

    def resolve(self, context, strategies):
        """Resolve a DynamicDependency that is not pinned to a specific version into one that is."""
        # avoid import cycle
        from .resolvers import resolve_dependency

        resolve_dependency(self, context, strategies)


class BaseGitHubDependency(DynamicDependency, abc.ABC):
    """Base class for dynamic dependencies that reference a GitHub repo."""

    github: Optional[AnyUrl]

    repo_owner: Optional[str]  # Deprecated - use full URL
    repo_name: Optional[str]  # Deprecated - use full URL

    tag: Optional[str]
    ref: Optional[str]

    @property
    @abc.abstractmethod
    def is_unmanaged(self):
        pass

    @property
    def is_resolved(self):
        return bool(self.ref)

    @pydantic.root_validator
    def check_deprecated_fields(cls, values):
        if values.get("repo_owner") or values.get("repo_name"):
            logger.warning(
                "The dependency keys `repo_owner` and `repo_name` are deprecated. Use the full repo URL with the `github` key instead."
            )
        return values

    @pydantic.root_validator
    def check_complete(cls, values):
        assert values["ref"] is None, "Must not specify `ref` at creation."

        return _validate_github_parameters(values)

    @property
    def name(self):
        return f"Dependency: {self.github}"


class GitHubDynamicSubfolderDependency(BaseGitHubDependency):
    """A dependency expressed by a reference to a subfolder of a GitHub repo, which needs
    to be resolved to a specific ref. This is always an unmanaged dependency."""

    subfolder: str
    namespace_inject: Optional[str]
    namespace_strip: Optional[str]

    @property
    def is_unmanaged(self):
        return True

    def flatten(self, context: BaseProjectConfig) -> List[Dependency]:
        """Convert to a static dependency after resolution"""

        if not self.is_resolved:
            raise DependencyResolutionError(
                f"Dependency {self} is not resolved and cannot be flattened."
            )

        return [
            UnmanagedGitHubRefDependency(
                github=self.github,
                ref=self.ref,
                subfolder=self.subfolder,
                namespace_inject=self.namespace_inject,
                namespace_strip=self.namespace_strip,
            )
        ]

    @property
    def name(self):
        return f"Dependency: {self.github}/{self.subfolder}"

    @property
    def description(self):
        loc = f" @{self.tag or self.ref}" if self.ref or self.tag else ""
        return f"{self.github}/{self.subfolder}{loc}"


class GitHubDynamicDependency(BaseGitHubDependency):
    """A dependency expressed by a reference to a GitHub repo, which needs
    to be resolved to a specific ref and/or package version."""

    unmanaged: bool = False
    namespace_inject: Optional[str]
    namespace_strip: Optional[str]
    password_env_name: Optional[str]

    skip: List[str] = []

    @property
    def is_unmanaged(self):
        return self.unmanaged

    @pydantic.validator("skip", pre=True)
    def listify_skip(cls, v):
        if v and not isinstance(v, list):
            v = [v]
        return v

    @pydantic.root_validator
    def check_unmanaged_values(cls, values):
        if not values.get("unmanaged") and (
            values.get("namespace_inject") or values.get("namespace_strip")
        ):
            raise ValueError(
                "The namespace_strip and namespace_inject fields require unmanaged = True"
            )

        return values

    def _flatten_unpackaged(
        self,
        repo: Repository,
        subfolder: str,
        skip: List[str],
        managed: bool,
        namespace: Optional[str],
    ) -> List[StaticDependency]:
        """Locate unmanaged dependencies from a repository subfolder (such as unpackaged/pre or unpackaged/post)"""
        unpackaged = []
        try:
            contents = repo.directory_contents(subfolder, return_as=dict, ref=self.ref)
        except NotFoundError:
            contents = None

        if contents:
            for dirname in sorted(contents.keys()):
                this_subfolder = f"{subfolder}/{dirname}"
                if this_subfolder in skip:
                    continue

                unpackaged.append(
                    UnmanagedGitHubRefDependency(
                        github=self.github,
                        ref=self.ref,
                        subfolder=this_subfolder,
                        unmanaged=not managed,
                        namespace_inject=namespace if namespace and managed else None,
                        namespace_strip=namespace
                        if namespace and not managed
                        else None,
                    )
                )

        return unpackaged

    def flatten(self, context: BaseProjectConfig) -> List[Dependency]:
        """Find more dependencies based on repository contents.

        Includes:
        - dependencies from cumulusci.yml
        - subfolders of unpackaged/pre
        - the contents of src, if this is not a managed package
        - subfolders of unpackaged/post
        """
        if not self.is_resolved:
            raise DependencyResolutionError(
                f"Dependency {self} is not resolved and cannot be flattened."
            )

        deps = []

        context.logger.info(f"Collecting dependencies from Github repo {self.github}")
        repo = get_repo(self.github, context)

        package_config = get_remote_project_config(repo, self.ref)
        _, namespace = get_package_data(package_config)

        # Parse upstream dependencies from the repo's cumulusci.yml
        # These may be unresolved or unflattened; if so, `get_static_dependencies()`
        # will manage them.
        dependencies = package_config.project__dependencies
        if dependencies:
            deps.extend([parse_dependency(d) for d in dependencies])
            if None in deps:
                raise DependencyResolutionError(
                    f"Unable to flatten dependency {self} because a transitive dependency could not be parsed."
                )

        # Check for unmanaged flag on a namespaced package
        managed = bool(namespace and not self.unmanaged)

        # Look for subfolders under unpackaged/pre
        # unpackaged/pre is always deployed unmanaged, no namespace manipulation.
        deps.extend(
            self._flatten_unpackaged(
                repo, "unpackaged/pre", self.skip, managed=False, namespace=None
            )
        )

        # Look for metadata under src (deployed if no namespace, or we're asked to do an unmanaged install)
        if not managed:
            contents = repo.directory_contents("src", ref=self.ref)
            if contents:
                deps.append(
                    UnmanagedGitHubRefDependency(
                        github=self.github,
                        ref=self.ref,
                        subfolder="src",
                        unmanaged=self.unmanaged,
                        namespace_inject=self.namespace_inject,
                        namespace_strip=self.namespace_strip,
                    )
                )
        else:
            if self.managed_dependency is None:
                raise DependencyResolutionError(
                    f"Could not find latest release for {self}"
                )

            deps.append(self.managed_dependency)

        # We always inject the project's namespace into unpackaged/post metadata if managed
        deps.extend(
            self._flatten_unpackaged(
                repo,
                "unpackaged/post",
                self.skip,
                managed=managed,
                namespace=namespace,
            )
        )

        return deps

    @property
    def description(self):
        unmanaged = " (unmanaged)" if self.unmanaged else ""
        loc = f" @{self.tag or self.ref}" if self.ref or self.tag else ""
        return f"{self.github}{unmanaged}{loc}"


class PackageNamespaceVersionDependency(StaticDependency):
    """Static dependency on a package identified by namespace and version number."""

    namespace: str
    version: str
    package_name: Optional[str]

    password_env_name: Optional[str]

    @property
    def package(self):
        return self.package_name or self.namespace or "Unknown Package"

    def install(
        self,
        context: BaseProjectConfig,
        org: OrgConfig,
        options: PackageInstallOptions = None,
        retry_options=None,
    ):
        if not options:
            options = PackageInstallOptions()
        if self.password_env_name:
            options.password = os.environ.get(self.password_env_name)
        if not retry_options:
            retry_options = DEFAULT_PACKAGE_RETRY_OPTIONS

        if "Beta" in self.version:
            version_string = self.version.split(" ")[0]
            beta = self.version.split(" ")[-1].strip(")")
            version = f"{version_string}b{beta}"
        else:
            version = self.version

        if org.has_minimum_package_version(
            self.namespace,
            version,
        ):
            context.logger.info(
                f"{self} or a newer version is already installed; skipping."
            )
            return

        context.logger.info(f"Installing {self.description}")
        install_package_by_namespace_version(
            context,
            org,
            self.namespace,
            self.version,
            options,
            retry_options=retry_options,
        )

    @property
    def name(self):
        return f"Install {self.package} {self.version}"

    @property
    def description(self):
        return f"{self.package} {self.version}"


class PackageVersionIdDependency(StaticDependency):
    """Static dependency on a package identified by 04t version id."""

    version_id: str
    package_name: Optional[str]
    version_number: Optional[str]

    password_env_name: Optional[str]

    @property
    def package(self):
        return self.package_name or "Unknown Package"

    def install(
        self,
        context: BaseProjectConfig,
        org: OrgConfig,
        options: PackageInstallOptions = None,
        retry_options=None,
    ):
        if not options:
            options = PackageInstallOptions()
        if self.password_env_name:
            options.password = os.environ.get(self.password_env_name)
        if not retry_options:
            retry_options = DEFAULT_PACKAGE_RETRY_OPTIONS

        if any(
            self.version_id == v.id
            for v in itertools.chain(*org.installed_packages.values())
        ):
            context.logger.info(
                f"{self} or a newer version is already installed; skipping."
            )
            return

        context.logger.info(f"Installing {self.description}")
        install_package_by_version_id(
            context,
            org,
            self.version_id,
            options,
            retry_options=retry_options,
        )

    @property
    def name(self):
        return f"Install {self.description}"

    @property
    def description(self):
        return f"{self.package} {self.version_number or self.version_id}"


class UnmanagedDependency(StaticDependency, abc.ABC):
    """Abstract base class for static, unmanaged dependencies."""

    unmanaged: Optional[bool]
    subfolder: Optional[str]
    namespace_inject: Optional[str]
    namespace_strip: Optional[str]

    def _get_unmanaged(self, org: OrgConfig):
        if self.unmanaged is None:
            if self.namespace_inject:
                return self.namespace_inject not in org.installed_packages
            else:
                return True

        return self.unmanaged

    @abc.abstractmethod
    def _get_zip_src(self, context: BaseProjectConfig):
        pass

    def install(self, context: BaseProjectConfig, org: OrgConfig):
        zip_src = self._get_zip_src(context)

        context.logger.info(f"Deploying unmanaged metadata from {self.description}")

        # Determine whether to inject namespace prefixes or not
        # If and only if we have no explicit configuration.

        options = {
            "unmanaged": self._get_unmanaged(org),
            "namespace_inject": self.namespace_inject,
            "namespace_strip": self.namespace_strip,
        }

        package_zip = MetadataPackageZipBuilder.from_zipfile(
            zip_src, options=options, logger=logger
        ).as_base64()
        task = TaskContext(org_config=org, project_config=context, logger=logger)

        api = ApiDeploy(task, package_zip)
        return api()


class UnmanagedGitHubRefDependency(UnmanagedDependency):
    """Static dependency on unmanaged metadata in a specific GitHub ref and subfolder."""

    repo_owner: Optional[str]
    repo_name: Optional[str]

    # or
    github: Optional[AnyUrl]

    # and
    ref: str

    @pydantic.root_validator
    def validate(cls, values):
        return _validate_github_parameters(values)

    def _get_zip_src(self, context):
        return download_extract_github_from_repo(
            get_repo(self.github, context),
            self.subfolder,
            ref=self.ref,
        )

    @property
    def name(self):
        subfolder = (
            f"/{self.subfolder}" if self.subfolder and self.subfolder != "src" else ""
        )
        return f"Deploy {self.github}{subfolder}"

    @property
    def description(self):
        subfolder = (
            f"/{self.subfolder}" if self.subfolder and self.subfolder != "src" else ""
        )

        return f"{self.github}{subfolder} @{self.ref}"


class UnmanagedZipURLDependency(UnmanagedDependency):
    """Static dependency on unmanaged metadata downloaded as a zip file from a URL."""

    zip_url: AnyUrl

    def _get_zip_src(self, context: BaseProjectConfig):
        return download_extract_zip(self.zip_url, subfolder=self.subfolder)

    @property
    def name(self):
        subfolder = f"/{self.subfolder}" if self.subfolder else ""
        return f"Deploy {self.zip_url} {subfolder}"

    @property
    def description(self):
        subfolder = f"/{self.subfolder}" if self.subfolder else ""
        return f"{self.zip_url} {subfolder}"


def parse_dependencies(deps: Optional[List[dict]]) -> List[Dependency]:
    """Convert a list of dependency specifications in the form of dicts
    (as defined in `cumulusci.yml`) and parse each into a concrete Dependency subclass.

    Throws DependencyParseError if a dict cannot be parsed."""
    parsed_deps = []
    for dep in deps or []:
        parsed = parse_dependency(dep)
        if parsed is None:
            raise DependencyParseError(f"Unable to parse dependency: {dep}")
        parsed_deps.append(parsed)
    return parsed_deps


def parse_dependency(dep_dict: dict) -> Optional[Dependency]:
    """Parse a single dependency specification in the form of a dict
    into a concrete Dependency subclass.

    Returns None if the given dict cannot be parsed."""

    # The order in which we attempt parsing is significant.
    # GitHubDynamicDependency has an optional `ref` field, but we want
    # any dependencies with a populated `ref` to be parsed as static deps.

    for dependency_class in [
        PackageNamespaceVersionDependency,
        PackageVersionIdDependency,
        UnmanagedGitHubRefDependency,
        UnmanagedZipURLDependency,
        GitHubDynamicDependency,
        GitHubDynamicSubfolderDependency,
    ]:
        try:
            dep = dependency_class.parse_obj(dep_dict)
            if dep:
                return dep
        except pydantic.ValidationError:
            pass
