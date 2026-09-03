import re

with open("pyproject.toml") as f:
    content = f.read()

content = re.sub(r'"types-requests==[^"]*",?\n?\s*', "", content)

with open("pyproject.toml", "w") as f:
    f.write(content)

print("Removed types-requests")
