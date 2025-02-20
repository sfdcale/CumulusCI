import click
import json

from pathlib import Path

from cumulusci.core.config import ServiceConfig
from cumulusci.core.exceptions import CumulusCIException, ServiceNotConfigured
from cumulusci.core.utils import import_global, import_class
from .runtime import pass_runtime
from .ui import CliTable


@click.group("service", help="Commands for connecting services to the keychain")
def service():
    pass


# Commands for group: service
@service.command(name="list", help="List services available for configuration and use")
@click.option("--plain", is_flag=True, help="Print the table using plain ascii.")
@click.option("--json", "print_json", is_flag=True, help="Print a json string")
@pass_runtime(require_project=False, require_keychain=True)
def service_list(runtime, plain, print_json):
    services = (
        runtime.project_config.services
        if runtime.project_config is not None
        else runtime.universal_config.services
    )
    supported_service_types = list(services.keys())
    supported_service_types.sort()

    if print_json:
        click.echo(json.dumps(services))
        return None

    configured_services = runtime.keychain.list_services()
    plain = plain or runtime.universal_config.cli__plain_output

    data = [["Type", "Name", "Default", "Description"]]

    for service_type in supported_service_types:
        if service_type not in configured_services:
            data.append(
                [service_type, "", False, services[service_type]["description"]]
            )
            continue
        for alias in configured_services[service_type]:
            try:
                default_service_for_type = runtime.keychain._default_services[
                    service_type
                ]
            except KeyError:
                default_service_for_type = None
            data.append(
                [
                    service_type,
                    alias,
                    alias == default_service_for_type,
                    services[service_type]["description"],
                ]
            )

    rows_to_dim = [row_index for row_index, row in enumerate(data) if not row[1]]
    table = CliTable(
        data,
        title="Services",
        wrap_cols=["Description"],
        bool_cols=["Default"],
        dim_rows=rows_to_dim,
    )
    table.echo(plain)


class ConnectServiceCommand(click.MultiCommand):
    def _get_services_config(self, runtime):
        return (
            runtime.project_config.services
            if runtime.project_config
            else runtime.universal_config.services
        )

    def list_commands(self, ctx):
        """list the services that can be configured"""
        runtime = ctx.obj
        services = self._get_services_config(runtime)
        return sorted(services.keys())

    def _build_param(self, attribute, details):
        req = details["required"]
        return click.Option((f"--{attribute}",), prompt=req, required=req)

    def _get_default_options(self, runtime):
        options = []
        options.append(
            click.Option(
                ("--default",),
                is_flag=True,
                help="Set this service as the global defualt.",
            )
        )
        if runtime.project_config is not None:
            options.append(
                click.Option(
                    ("--project",),
                    is_flag=True,
                    help="Set this service as the default for this project only.",
                )
            )
        return options

    def get_command(self, ctx, service_type):
        runtime = ctx.obj
        runtime._load_keychain()
        services = self._get_services_config(runtime)

        try:
            service_config = services[service_type]
        except KeyError:
            raise click.UsageError(
                f"Sorry, I don't know about the '{service_type}' service."
            )

        attributes = service_config["attributes"].items()
        params = [self._build_param(attr, cnfg) for attr, cnfg in attributes]
        params.extend(self._get_default_options(runtime))

        def callback(*args, **kwargs):
            service_name = kwargs.get("service_name")
            if not service_name:
                click.echo(
                    "No service name specified. Using 'default' as the service name."
                )
                service_name = "default"

            configured_services = runtime.keychain.list_services()
            if (
                service_type in configured_services
                and service_name in configured_services[service_type]
            ):
                click.confirm(
                    f"There is already a {service_type}:{service_name} service. Do you want to overwrite it?",
                    abort=True,
                )

            if runtime.project_config is None:
                set_project_default = False
            else:
                set_project_default = kwargs.pop("project", False)

            set_global_default = kwargs.pop("default", False)

            serv_conf = dict(
                (k, v) for k, v in list(kwargs.items()) if v is not None
            )  # remove None values

            # A service can define a callable to validate the service config
            validator_path = service_config.get("validator")
            if validator_path:
                validator = import_global(validator_path)
                validator(serv_conf)

            ConfigClass = ServiceConfig
            if "class_path" in service_config:
                class_path = service_config["class_path"]
                try:
                    ConfigClass = import_class(class_path)
                except (AttributeError, ModuleNotFoundError):
                    raise CumulusCIException(
                        f"Unrecognized class_path for service: {class_path}"
                    )
                # Establish OAuth2 connection if required by this service
                if hasattr(ConfigClass, "connect"):
                    oauth_dict = ConfigClass.connect(runtime.keychain, kwargs)
                    serv_conf.update(oauth_dict)

            config_instance = ConfigClass(serv_conf, service_name, runtime.keychain)

            runtime.keychain.set_service(
                service_type,
                service_name,
                config_instance,
            )
            click.echo(f"Service {service_type}:{service_name} is now connected")

            if set_global_default:
                runtime.keychain.set_default_service(
                    service_type, service_name, project=False
                )
                click.echo(
                    f"Service {service_type}:{service_name} is now the default for all CumulusCI projects"
                )
            if set_project_default:
                runtime.keychain.set_default_service(
                    service_type, service_name, project=True
                )
                project_name = runtime.project_config.project__name
                click.echo(
                    f"Service {service_type}:{service_name} is now the default for project '{project_name}'"
                )

        params.append(click.Argument(["service_name"], required=False))
        return click.Command(service_type, params=params, callback=callback)


@service.command(
    cls=ConnectServiceCommand,
    name="connect",
    help="Connect an external service to CumulusCI",
)
def service_connect():
    pass


@service.command(name="info", help="Show the details of a connected service")
@click.argument("service_type")
@click.argument("service_name", required=False)
@click.option("--plain", is_flag=True, help="Print the table using plain ascii.")
@pass_runtime(require_project=False, require_keychain=True)
def service_info(runtime, service_type, service_name, plain):
    try:
        plain = plain or runtime.universal_config.cli__plain_output
        service_config = runtime.keychain.get_service(service_type, service_name)
        service_data = [["Key", "Value"]]
        service_data.extend(
            [
                [click.style(k, bold=True), str(v)]
                for k, v in service_config.config.items()
                if k != "service_name"
            ]
        )
        wrap_cols = ["Value"] if not plain else None
        default_service = runtime.keychain.get_default_service_name(service_type)
        service_name = default_service if not service_name else service_name
        service_table = CliTable(
            service_data, title=f"{service_type}:{service_name}", wrap_cols=wrap_cols
        )
        service_table._table.inner_heading_row_border = False
        service_table.echo(plain)
    except ServiceNotConfigured:
        click.echo(
            f"{service_type} is not configured for this project.  Use service connect {service_type} to configure."
        )


@service.command(
    name="default", help="Set the default service for a given service type."
)
@click.argument("service_type")
@click.argument("service_name")
@click.option(
    "--project",
    is_flag=True,
    help="Sets the service as the default for the current project.",
)
@pass_runtime(require_project=False, require_keychain=True)
def service_default(runtime, service_type, service_name, project):
    try:
        runtime.keychain.set_default_service(service_type, service_name, project)
    except ServiceNotConfigured as e:
        click.echo(f"An error occurred setting the default service: {e}")
        return
    if project:
        project_name = Path(runtime.keychain.project_local_dir).name
        click.echo(
            f"Service {service_type}:{service_name} is now the default for project '{project_name}'"
        )
    else:
        click.echo(
            f"Service {service_type}:{service_name} is now the default for all CumulusCI projects"
        )


@service.command(name="rename", help="Rename a service")
@click.argument("service_type")
@click.argument("current_name")
@click.argument("new_name")
@pass_runtime(require_project=False, require_keychain=True)
def service_rename(runtime, service_type, current_name, new_name):
    try:
        runtime.keychain.rename_service(service_type, current_name, new_name)
    except ServiceNotConfigured as e:
        click.echo(f"An error occurred renaming the service: {e}")
        return

    click.echo(f"Service {service_type}:{current_name} has been renamed to {new_name}")


@service.command(name="remove", help="Remove a service")
@click.argument("service_type")
@click.argument("service_name")
@pass_runtime(require_project=False, require_keychain=True)
def service_remove(runtime, service_type, service_name):
    new_default = None
    if len(
        runtime.keychain.services.get(service_type, {}).keys()
    ) > 2 and service_name == runtime.keychain._default_services.get(service_type):
        click.echo(
            f"The service you would like to remove is currently the default for {service_type} services."
        )
        click.echo("Your other services of the same type are:")
        for alias in runtime.keychain.list_services()[service_type]:
            if alias != service_name:
                click.echo(alias)
        new_default = click.prompt(
            "Enter the name of the service you would like as the new default"
        )
        if new_default not in runtime.keychain.list_services()[service_type]:
            click.echo(f"No service of type {service_type} with name: {new_default}")
            return

    try:
        runtime.keychain.remove_service(service_type, service_name)
        if new_default:
            runtime.keychain.set_default_service(service_type, new_default)
    except ServiceNotConfigured as e:
        click.echo(f"An error occurred removing the service: {e}")
        return

    click.echo(f"Service {service_type}:{service_name} has been removed.")
