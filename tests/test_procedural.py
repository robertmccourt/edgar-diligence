from edgar.memory.procedural import SECTIONS, load_rubric, load_system_prompt


def test_eleven_sections_with_stable_slugs():
    assert len(SECTIONS) == 11
    assert [s[1] for s in SECTIONS][:3] == ["business", "growth",
                                            "profitability"]


def test_every_section_has_a_nonempty_rubric():
    for _, slug, _ in SECTIONS:
        text = load_rubric(slug)
        assert len(text) > 100, f"rubric {slug} too thin"


def test_system_prompt_states_the_three_laws():
    text = load_system_prompt()
    for needle in ("cite", "as_of", "compute"):
        assert needle in text.lower()
