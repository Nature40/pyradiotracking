import logging
import sys
from argparse import ArgumentParser, Namespace
from ast import literal_eval
from configparser import ConfigParser
from typing import Any, Iterable, List, Optional, Sequence, Text, TextIO, Tuple, Dict


class ArgConfParser(ArgumentParser):
    """
    An argparse.ArgumentParser subclass that reads a config file and updates the namespace accordingly.

    Parameters
    ----------
    config_dest: str
        The name of the config file to be read.
    """

    def __init__(self, *args, config_dest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_dest = config_dest

    def parse_known_args(self, args: Optional[Sequence[Text]] = None, namespace: Optional[Namespace] = None) -> Tuple[Namespace, List[str]]:
        """
        Initialize defaults according to the parser, update with config file and then parse the arguments. 

        Parameters
        ----------
        args: typing.Optional[typing.Sequence[str]]
            The list of arguments to parse.
        namespace: typing.Optional[argparse.Namespace]
            An optional initial namespace.

        Returns
        -------
        argparse.Namespace
            The parsed namespace.
        typing.List[str]
            The remaining arguments.
        """
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

    def immutable_args(self, args: Optional[Sequence[Text]] = None) -> Iterable[str]:
        """
        Get a list of immutable arguments.

        Parameters
        ----------
        args: typing.Optional[typing.Sequence[str]]
            The list of arguments to parse, if unspecified, sys.argv is used.

        Returns
        -------
        typing.Iterable[str]
        """
        if args is None:
            # args default to the system args
            args = sys.argv[1:]
        else:
            # make sure that args are mutable
            args = list(args)

        namespace = Namespace()
        namespace, _ = super()._parse_known_args(arg_strings=args, namespace=namespace)

        return namespace.__dict__.keys()

    def read_config(self, path: str) -> Dict[str, Any]:
        """
        Read a config file and update the namespace accordingly.

        Parameters
        ----------
        path: str
            The path to the config file.

        Returns
        -------
        typing.Dict[str, typing.Any]
            The configuration read from the file.
        """
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

    def write_config(self, args: Namespace, file: TextIO, help: bool = False):
        """
        Writes the current namespace to a config file.

        Parameters
        ----------
        args: argparse.Namespace
            The namespace to write.
        file: typing.TextIO
            The file to write to.
        help: bool
            Whether to write the help text.
        """
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


if __name__ == "__main__":
    import radiotracking.present
    from radiotracking.__main__ import Runner

    parser: ArgConfParser = Runner.parser
    args = parser.parse_args()

    # logging levels increase in steps of 10, start with warning
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    dashboard = radiotracking.present.ConfigDashboard(args, parser.immutable_args(), **args.__dict__)

    dashboard.run()
