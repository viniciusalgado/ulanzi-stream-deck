"""The on-screen Play button finder, on drawn stand-ins for Spotify's playlist page (no real screenshots)."""
import io

from PIL import Image, ImageDraw

from deck.sources.spotify_ui import CHANGED, STEADY, cover_on_page, find_play_button, header_change

GREEN = (30, 215, 96)


def cover(seed: int) -> Image.Image:
    img = Image.new("RGB", (300, 300), (40 * seed % 255, 90, 160))
    d = ImageDraw.Draw(img)
    for i in range(6):
        d.ellipse((20 + 37 * i, (seed * 53 + 41 * i) % 220, 90 + 37 * i, (seed * 53 + 41 * i) % 220 + 70),
                  fill=((seed * 97 + i * 60) % 255, (i * 40) % 255, (seed * 31) % 255))
    return img


def page(cover_img: Image.Image, glyph: str) -> Image.Image:
    img = Image.new("RGB", (1000, 750), (18, 18, 18))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((88, 64, 700, 660), 8, fill=(60, 20, 30))                # main panel
    img.paste(cover_img.resize((132, 132)), (104, 107))                          # header cover
    d.ellipse((104, 272, 160, 328), fill=GREEN)                                   # big Play button
    if glyph == "play":
        d.polygon([(124, 286), (124, 314), (146, 300)], fill=(0, 0, 0))
    else:
        d.rectangle((121, 288, 127, 312), fill=(0, 0, 0))
        d.rectangle((137, 288, 143, 312), fill=(0, 0, 0))
    d.ellipse((330, 288, 354, 312), fill=GREEN)                                   # small "saved" tick: ignored
    d.ellipse((20, 400, 60, 440), fill=GREEN)                                     # sidebar icon: ignored
    return img


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_finds_the_big_play_button_and_reads_its_icon():
    b = find_play_button(page(cover(1), "play"))
    assert b is not None and abs(b.center[0] - 132) <= 3 and abs(b.center[1] - 300) <= 3 and not b.playing
    assert find_play_button(page(cover(1), "pause")).playing


def test_only_the_right_playlists_cover_matches():
    shown = page(cover(1), "play")
    b = find_play_button(shown)
    assert cover_on_page(shown, b, png(cover(1))) < 0.06
    assert cover_on_page(shown, b, png(cover(2))) > 0.06


def test_no_button_on_an_empty_window():
    assert find_play_button(Image.new("RGB", (1000, 750), (18, 18, 18))) is None


def banner_page(seed: int) -> Image.Image:
    """A "This Is <artist>" page: a wide photo banner and the title over it, no square cover."""
    img = page(cover(1), "play")
    img.paste(cover(seed).resize((612, 200)), (88, 64))
    ImageDraw.Draw(img).rectangle((104, 200, 600, 240), fill=(250, 250, 250))       # the big title
    return img


def test_a_banner_page_has_no_matching_cover_but_its_header_shows_the_page_changed():
    old, new = page(cover(3), "play"), banner_page(5)
    b = find_play_button(new)
    assert cover_on_page(new, b, png(cover(5))) > 0.06                     # why the cover check alone can't work
    assert header_change(old, new, b) > CHANGED                            # switched away from the old page
    assert header_change(new, banner_page(5), b) < STEADY                  # and finished loading
    assert header_change(old, page(cover(3), "pause"), b) < CHANGED         # the old page, playing: not a switch
    assert header_change(None, new, b) == 1.0
