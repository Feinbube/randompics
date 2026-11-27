import re
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def render_photo_tile(tree: str) -> str:
    env = Environment(loader=FileSystemLoader(ROOT / "admin" / "templates"))
    template = env.from_string(
        '{% import "_macros.html" as m %}'
        '{{ m.photo_tile(photo, "forum", "csrf", "album:forum", album_label="VWP") }}'
    )
    photo = {
        "id": "20261103-120000",
        "tree": tree,
        "stamp": "03.11.2026 12:00",
        "score": None,
        "depth_issue": False,
        "depth_note": "",
    }
    return template.render(photo=photo)


def visibility_form(html: str) -> str:
    match = re.search(
        r'<form method="post" action="/admin/photo/visibility".*?</form>',
        html,
        re.S,
    )
    if not match:
        raise AssertionError("photo visibility form missing")
    return match.group(0)


class PhotoVisibilityToggleTest(unittest.TestCase):
    def test_making_a_photo_public_is_not_confirmed(self):
        """One click makes it public again; the toggle is the undo."""
        form = visibility_form(render_photo_tile("private"))
        self.assertIn('value="public"', form)
        self.assertNotIn("data-confirm", form)

    def test_making_a_photo_private_stays_unconfirmed(self):
        form = visibility_form(render_photo_tile("public"))
        self.assertIn('value="private"', form)
        self.assertNotIn("data-confirm", form)

    def test_delete_still_confirms(self):
        html = render_photo_tile("private")
        match = re.search(
            r'<form method="post" action="/admin/remove".*?</form>', html, re.S
        )
        assert match is not None
        self.assertIn("data-confirm", match.group(0))


if __name__ == "__main__":
    unittest.main()
