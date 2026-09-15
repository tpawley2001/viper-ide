import sys


def run() -> int:
    if "--selftest" in sys.argv:
        from viper_ide.selftest import main as selftest

        return selftest(sys.argv[sys.argv.index("--selftest") + 1:])
    from viper_ide.app import main

    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(run())
