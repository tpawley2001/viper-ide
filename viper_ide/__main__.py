import sys


def run() -> int:
    if "--selftest" in sys.argv:
        from viper_ide.selftest import main as selftest

        return selftest(sys.argv[sys.argv.index("--selftest") + 1:])
    if "--paths" in sys.argv:  # where settings and data go; the build checks this for the portable zip
        import json

        from viper_ide import paths

        info = {"portable": str(paths.portable_root() or ""), "config": str(paths.config_dir()),
                "data": str(paths.data_dir())}
        rest = sys.argv[sys.argv.index("--paths") + 1:]
        with open(rest[0], "w", encoding="utf-8") if rest else sys.stdout as out:
            json.dump(info, out, indent=2)
        return 0
    from viper_ide.app import main

    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(run())
