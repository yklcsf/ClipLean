import sys


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--konsol":
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
