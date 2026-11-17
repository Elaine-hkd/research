from pathlib import Path

desktop = Path.home() / "Desktop"
desktop.mkdir(parents=True, exist_ok=True)

output = desktop / "hello.txt"
output.write_text("hello", encoding="utf-8")
print(output)