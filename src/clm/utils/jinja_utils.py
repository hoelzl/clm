from functools import singledispatch
from collections.abc import Sequence

import jinja2
from jinja2 import FileSystemLoader

from clm.utils.location import Location, FileSystemLocation, InMemoryLocation


class LocationLoader(jinja2.BaseLoader):
    def __init__(self, location):
        self.location = location

    def get_source(self, environment, template) -> tuple[str, Location, callable]:
        template_loc = self.location / template
        try:
            with template_loc.open(encoding="utf-8") as file:
                source = file.read()
        except Exception:
            raise jinja2.TemplateNotFound(template)
        return source, template_loc.as_posix(), lambda: True


@singledispatch
def get_jinja_loader(location) -> jinja2.BaseLoader:
    raise NotImplementedError(f"Cannot create loader for {location}")


@get_jinja_loader.register
def _(location: FileSystemLocation) -> jinja2.BaseLoader:
    if location.exists():
        return FileSystemLoader(location.absolute())
    else:
        raise FileNotFoundError(f"Cannot create loader for {location}")


@get_jinja_loader.register
def _(location: InMemoryLocation) -> jinja2.BaseLoader:
    if location.exists():
        return LocationLoader(location)
    else:
        raise FileNotFoundError(f"Cannot create loader for {location}")


@get_jinja_loader.register
def _(location: Sequence) -> jinja2.BaseLoader:
    return jinja2.ChoiceLoader([get_jinja_loader(loc) for loc in location])
