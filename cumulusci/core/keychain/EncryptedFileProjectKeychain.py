from __future__ import print_function
import os

from cumulusci.core.exceptions import OrgNotFound
from cumulusci.core.exceptions import ServiceNotConfigured
from cumulusci.core.keychain import BaseEncryptedProjectKeychain


class EncryptedFileProjectKeychain(BaseEncryptedProjectKeychain):
    """ An encrypted project keychain that stores in the project's local directory """

    @property
    def config_local_dir(self):
        return os.path.join(
            os.path.expanduser('~'),
            self.project_config.global_config_obj.config_local_dir,
        )

    @property
    def project_local_dir(self):
        return self.project_config.project_local_dir

    def _load_files(self, dirname, extension, key):
        for item in sorted(os.listdir(dirname)):
            if item.endswith(extension):
                with open(os.path.join(dirname, item), 'r') as f_item:
                    config = f_item.read()
                name = item.replace(extension, '')
                if not key in self.config:
                    self.config[key] = []
                self.config[key][name] = config

    def _load_file(self, dirname, filename, key):
        full_path = os.path.join(dirname, filename)
        if not os.path.exists(full_path):
            return
        with open(os.path.join(dirname, filename), 'r') as f_item:
            config = f_item.read()
        self.config[key] = config

    def _load_orgs(self):
        self._load_files(self.config_local_dir, '.org', 'orgs')
        self._load_files(self.project_local_dir, '.org', 'orgs')

    def _load_services(self):
        self._load_files(self.config_local_dir, '.service', 'services')
        self._load_files(self.project_local_dir, '.service', 'services')

    def _remove_org(self, name, global_org):
        if global_org:
            full_path = os.path.join(self.config_local_dir, '{}.org'.format(name))
        else:
            full_path = os.path.join(self.project_local_dir, '{}.org'.format(name))
        if not os.path.exists(full_path):
            kwargs = {'name': name}
            if not global_org:
                raise OrgNotFound('Could not find org named {name} to delete.  Deleting in project org mode.  Is {name} a global org?'.format(**kwargs))
            raise OrgNotFound('Could not find org named {name} to delete.  Deleting in global org mode.  Is {name} a project org instead of a global org?'.format(**kwargs))
           
        os.remove(full_path) 
        self._load_orgs()

    def _set_encrypted_org(self, name, encrypted, global_org):
        if global_org:
            filename = os.path.join(
                self.config_local_dir, '{}.org'.format(name))
        else:
            filename = os.path.join(
                self.project_local_dir, '{}.org'.format(name))
        with open(filename, 'wb') as f_org:
            f_org.write(encrypted)

    def _set_encrypted_service(self, name, encrypted, project):
        if project:
            filename = os.path.join(
                self.project_local_dir, '{}.service'.format(name))
        else:
            filename = os.path.join(
                self.config_local_dir, '{}.service'.format(name))
        with open(filename, 'wb') as f_service:
            f_service.write(encrypted)

    def _raise_org_not_found(self, name):
        raise OrgNotFound(
            'Org information could not be found.  Expected to find encrypted file at {}/{}.org'.format(
                self.project_local_dir,
                name
            )
        )

    def _raise_service_not_configured(self, name):
        raise ServiceNotConfigured(
            'Service configuration could not be found.  Expected to find encrypted file at {}/{}.org'.format(
                self.project_local_dir,
                name
            )
        )
