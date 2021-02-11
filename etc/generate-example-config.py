from radiotracking.__main__ import Runner

args = Runner.parser.parse_args([])
with open("etc/radiotracking.ini", "w") as f:
    Runner.parser.write_config(args, f, help=True)
