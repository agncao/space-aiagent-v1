"""
从 pyproject.toml 生成 requirements.txt

用法: python scripts/gen_requirements.py
"""
import tomllib
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).parent.parent
    pyproject_path = project_root / "pyproject.toml"
    output_path = project_root / "requirements.txt"

    with open(pyproject_path, "rb") as f:
        pyproject = tomllib.load(f)

    dependencies = pyproject["project"].get("dependencies", [])
    dev_deps = pyproject["project"].get("optional-dependencies", {}).get("dev", [])

    lines = ["# Auto-generated from pyproject.toml - DO NOT EDIT MANUALLY", "# Run: python scripts/gen_requirements.py", ""]

    lines.append("# === Runtime Dependencies ===")
    lines.extend(dependencies)
    lines.append("")
    lines.append("# === Dev Dependencies ===")
    lines.extend(dev_deps)

    output_path.write_text("\n".join(lines) + "\n")
    print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
