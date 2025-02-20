import os
from typing import Optional
from pathlib import Path
import shutil
from contextlib import contextmanager


from cumulusci.core.utils import process_list_of_pairs_dict_arg, process_list_arg

from cumulusci.core.exceptions import TaskOptionsError
from cumulusci.tasks.bulkdata.base_generate_data_task import BaseGenerateDataTask
from snowfakery import generate_data


class GenerateDataFromYaml(BaseGenerateDataTask):
    """Generate sample data from a YAML template file."""

    task_docs = """
    Generate YAML from a Snowfakery recipe.
    """

    task_options = {
        **BaseGenerateDataTask.task_options,
        "generator_yaml": {
            "description": "A Snowfakery recipe file to use",
            "required": True,
        },
        "vars": {
            "description": "Pass values to override options in the format VAR1:foo,VAR2:bar"
        },
        "generate_mapping_file": {
            "description": "A path to put a mapping file inferred from the generator_yaml",
            "required": False,
        },
        "num_records_tablename": {
            "description": "A string representing which table determines when the recipe execution is done.",
            "required": False,
        },
        "continuation_file": {
            "description": "YAML file generated by Snowfakery representing next steps for data generation"
        },
        "generate_continuation_file": {
            "description": "Path for Snowfakery to put its next continuation file"
        },
        "working_directory": {
            "description": "Default path for temporary / working files"
        },
        "loading_rules": {
            "description": "Path to .load.yml file containing rules to use to load the file. Defaults to <recipename>.load.yml . Multiple files can be comma separated."
        },
        "num_records": {
            "description": (
                "Target number of records. "
                "You will get at least this many records, but may get more. "
                "The recipe will always execute to completion, so if it creates "
                "3 records per execution and you ask for 5, you will get 6."
            ),
            "required": False,
        },
    }
    stopping_criteria = None

    def __init__(self, *args, **kwargs):
        self.vars = {}
        super().__init__(*args, **kwargs)

    def _init_options(self, kwargs):
        super()._init_options(kwargs)
        self.yaml_file = os.path.abspath(self.options["generator_yaml"])
        if not os.path.exists(self.yaml_file):
            raise TaskOptionsError(f"Cannot find {self.yaml_file}")
        if "vars" in self.options:
            self.vars = process_list_of_pairs_dict_arg(self.options["vars"])
        self.generate_mapping_file = self.options.get("generate_mapping_file")
        if self.generate_mapping_file:
            self.generate_mapping_file = os.path.abspath(self.generate_mapping_file)
        num_records = self.options.get("num_records")
        if num_records is not None:
            num_records = int(num_records)
            num_records_tablename = self.options.get("num_records_tablename")

            if not num_records_tablename:
                raise TaskOptionsError(
                    "Cannot specify num_records without num_records_tablename."
                )

            self.stopping_criteria = (num_records, num_records_tablename)
        self.working_directory = self.options.get("working_directory")
        loading_rules = process_list_arg(self.options.get("loading_rules")) or []
        self.loading_rules = [Path(path) for path in loading_rules if path]

    def _generate_data(self, db_url, mapping_file_path, num_records, current_batch_num):
        """Generate all of the data"""
        if num_records is not None:  # num_records is None means execute Snowfakery once
            self.logger.info(f"Generating batch {current_batch_num} with {num_records}")
        self.generate_data(db_url, num_records, current_batch_num)
        self.logger.info("Generated batch")

    def default_continuation_file_path(self):
        return Path(self.working_directory) / "continuation.yml"

    def get_old_continuation_file(self) -> Optional[Path]:
        """Use a continuation file if specified or look for one in the working directory

        Return None if no file can be found.

        If this code is used within a GenerateAndLoad loop, the continuation files will go
        into the working directory specified by the GenerateAndLoad caller.
        """
        old_continuation_file = self.options.get("continuation_file")

        if old_continuation_file:
            old_continuation_file = Path(old_continuation_file)
            if not old_continuation_file.exists():
                raise TaskOptionsError(f"{old_continuation_file} does not exist")
        elif self.working_directory:
            path = self.default_continuation_file_path()
            if path.exists():
                old_continuation_file = path

        return old_continuation_file

    @contextmanager
    def open_new_continuation_file(self):
        """Create a continuation file based on config or working directory

        Return None if there is no config nor working directory.

        If this code is used within a GenerateAndLoad loop, the continuation files will go
        into the working directory specified by the GenerateAndLoad caller.
        """
        if self.options.get("generate_continuation_file"):
            continuation_path = self.options["generate_continuation_file"]

        elif self.working_directory:
            continuation_path = Path(self.working_directory) / "continuation_next.yml"
        else:
            continuation_path = None

        if continuation_path:
            with open(continuation_path, "w+") as new_continuation_file:
                yield new_continuation_file
        else:
            yield None

    def generate_data(self, dburl, num_records, current_batch_num):
        old_continuation_file = self.get_old_continuation_file()
        if old_continuation_file:
            # reopen to ensure file pointer is at starting point
            old_continuation_file = open(old_continuation_file, "r")
        with self.open_new_continuation_file() as new_continuation_file:
            generate_data(
                yaml_file=self.yaml_file,
                user_options=self.vars,
                target_number=self.stopping_criteria,
                continuation_file=old_continuation_file,
                generate_continuation_file=new_continuation_file,
                generate_cci_mapping_file=self.generate_mapping_file,
                dburl=dburl,
                load_declarations=self.loading_rules,
                should_create_cci_record_type_tables=True,
                plugin_options={"orgname": self.org_config.name},
            )

        if (
            new_continuation_file
            and Path(new_continuation_file.name).exists()
            and self.working_directory
        ):
            shutil.move(
                new_continuation_file.name, self.default_continuation_file_path()
            )
