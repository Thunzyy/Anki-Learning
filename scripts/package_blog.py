"""Package the French and English MDX articles with their shared public images."""

from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    blog = ROOT / "docs" / "blog"
    articles = [blog / f"learning-anki.{locale}.mdx" for locale in ("fr", "en")]
    text = "\n".join(article.read_text(encoding="utf-8") for article in articles)
    assets = json.loads((blog / "assets.json").read_text(encoding="utf-8"))
    images = sorted(name for name, url in assets.items() if url in text)
    sources = [(ROOT / "docs" / "images" / name, f"public/images/learning/{name}") for name in images]
    sources += [(article, article.name) for article in articles]
    sources.append((blog / "assets.json", "assets.json"))
    for source, _ in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    output = ROOT / "dist" / "learning-blog.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", "Import the FR and EN MDX files into the matching locales of one blog post.\nImages are already hosted publicly on the portfolio media storage.\nThe public/ folder contains backup originals; assets.json maps them to their public URLs.\n")
        for source, destination in sources:
            archive.write(source, destination)
    print(f"Created {output} ({len(articles)} articles, {len(images)} shared images)")


if __name__ == "__main__":
    main()
