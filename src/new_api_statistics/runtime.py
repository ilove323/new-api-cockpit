"""Apply monitoring migrations once before starting a web/worker process.

Docker's entrypoint and non-Docker deployments use the same startup boundary.
Normal requests never execute DDL. No New API source writes are performed.
"""

import logging
import os
import sys

from new_api_statistics import balance


def main():
    if balance.configured():
        try:
            balance.initialize()
        except Exception as exc:
            logging.error("Monitoring schema startup failed (%s)", type(exc).__name__)
            raise SystemExit(1) from None
    if len(sys.argv) > 1:
        command = sys.argv[1:]
        if os.path.basename(command[0]) == "gunicorn" and not any(
            arg in {"-c", "--config"} or arg.startswith("--config=")
            for arg in command[1:]
        ):
            command = [
                command[0],
                "--config",
                "python:new_api_statistics.gunicorn_conf",
                *command[1:],
            ]
        os.execvp(command[0], command)


if __name__ == "__main__":
    main()
