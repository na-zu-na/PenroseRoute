"""Switch only backend/.env's database name; never print its credentials."""

import argparse
import re
from pathlib import Path


DATABASES = {"original": b"penrose_route", "rebuilt": b"penrose_route_demo_rebuilt"}


def switch(target: str) -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    content = env_file.read_bytes()
    pattern = re.compile(rb"(?m)^(DATABASE_URL=[^\r\n]*/)(penrose_route(?:_demo_rebuilt)?)([\"']?\r?)$")
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        raise ValueError("Expected exactly one local PenroseRoute DATABASE_URL in backend/.env")
    match = matches[0]
    database = DATABASES[target]
    if match.group(2) == database:
        print(f"backend/.env already points to {target} database")
        return
    env_file.write_bytes(content[:match.start(2)] + database + content[match.end(2):])
    print(f"backend/.env now points to {target} database; restart backend to apply")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=DATABASES)
    switch(parser.parse_args().target)
