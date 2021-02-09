from argparse import ArgumentParser, Namespace
from ast import literal_eval
from configparser import ConfigParser
from io import TextIOWrapper
from typing import List, Optional, Sequence, Text, Tuple


class ArgConfParser(ArgumentParser):
    def __init__(self, *args, config_dest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_dest = config_dest

    def parse_known_args(self, args: Optional[Sequence[Text]] = None, namespace: Optional[Namespace] = None) -> Tuple[Namespace, List[str]]:
        # create namespace filled with default values
        namespace, _ = super().parse_known_args(args=[], namespace=namespace)

        # read config parameter to separate namespace
        config_namespace, _ = super().parse_known_args(args=args)

        # read config file if specified and update namespace
        if self.config_dest in config_namespace.__dict__:
            config = self.read_config(config_namespace.__dict__[self.config_dest])
            namespace.__dict__.update(config)

        # parse args and update namespace
        namespace, unparsed = super().parse_known_args(args=args, namespace=namespace)

        return (namespace, unparsed)

    def read_config(self, path):
        config = ConfigParser()
        config.read(path)

        conf_dict = {}

        for group in self._action_groups:
            # skip untitled groups
            if not isinstance(group.title, str):
                continue

            # skip groups not used in the config file
            if group.title not in config:
                continue

            # iterate actions and extract values
            for action in group._group_actions:
                if action.dest in config[group.title]:
                    conf_dict[action.dest] = literal_eval(config[group.title][action.dest])

        return conf_dict

    def write_config(self, args: Namespace, file: TextIOWrapper, help: bool = False):
        config = ConfigParser(allow_no_value=help)

        for group in self._action_groups:
            # skip unnamed groups
            if not isinstance(group.title, str):
                continue

            # skip empty groups
            if not group._group_actions:
                continue

            # create section for group title
            config[group.title] = {}

            # iterate actions and set config accordingly
            for action in group._group_actions:
                # extract parameters from args and set in config
                if action.dest in args.__dict__:
                    if help:
                        config.set(group.title, f"# {action.help}")
                    config[group.title][action.dest] = repr(args.__dict__[action.dest])

        config.write(file)
