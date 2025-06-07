import os
import jinja2


env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(searchpath=os.path.dirname(__file__)),
    autoescape=False
)