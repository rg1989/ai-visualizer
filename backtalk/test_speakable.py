"""The one check that fails if markdown reaches the voice again."""
from backtalk.main import _speakable


def test_speakable():
    assert _speakable("is a **pale green**, near-white") == "is a pale green, near-white"
    assert _speakable("the *only* one and __this__") == "the only one and this"
    assert _speakable("`glass.sh` is fine") == "glass.sh is fine"
    assert _speakable("1.") == "" and _speakable("- eggs") == "eggs" and _speakable("2) basil") == "basil"
    assert _speakable("## Heading text") == "Heading text"
    assert _speakable("2 * 3 = 6 and a_b_c stays") == "2 * 3 = 6 and a_b_c stays"
    assert _speakable("Done -- DANGER is up.") == "Done -- DANGER is up."
    assert _speakable("[silent]") == "" and _speakable("[SILENT].") == "" and _speakable("silent") == ""
    assert _speakable("Silent as the grave.") == "Silent as the grave."
    print("ok: markdown stripped, arithmetic and identifiers untouched, [silent] swallowed")


if __name__ == "__main__":
    test_speakable()
