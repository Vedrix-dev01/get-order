from content_agents import media


def test_parse_silencedetect_handles_trailing_silence():
    stderr = """
[silencedetect @ 0x1] silence_start: -0.002
[silencedetect @ 0x1] silence_end: 1.5 | silence_duration: 1.5
[silencedetect @ 0x1] silence_start: 4.2
[silencedetect @ 0x1] silence_end: 5.1 | silence_duration: 0.9
[silencedetect @ 0x1] silence_start: 9.0
"""
    assert media.parse_silencedetect(stderr, 10.0) == [(0.0, 1.5), (4.2, 5.1), (9.0, 10.0)]


def test_speech_segments_are_padded_complement_of_silence():
    segs = media.speech_segments(10.0, [(0.0, 1.5), (4.2, 5.1), (9.0, 10.0)], pad=0.1)
    assert segs == [(1.4, 4.3), (5.0, 9.1)]


def test_speech_segments_merge_when_padding_overlaps_and_drop_blips():
    segs = media.speech_segments(6.0, [(2.0, 2.15), (3.0, 5.9)], pad=0.1, min_keep=0.2)
    assert segs == [(0.0, 3.1)]  # the 2.0-2.15 gap is swallowed by padding; 5.9-6.0 tail too short


def test_speech_segments_with_no_silence_keeps_everything():
    assert media.speech_segments(5.0, []) == [(0.0, 5.0)]


def test_map_time_onto_cut_timeline():
    segs = [(1.0, 3.0), (5.0, 6.0)]
    assert media.map_time(2.0, segs) == 1.0
    assert media.map_time(5.5, segs) == 2.5
    assert media.map_time(4.0, segs) is None  # inside a removed pause


def test_align_sentences_one_per_segment():
    cards = media.align_sentences(["One.", "Two two."], [(1.0, 3.0), (5.0, 6.5)])
    assert [(c.start, c.end, c.text) for c in cards] == [(0.0, 2.0, "One."), (2.0, 3.5, "Two two.")]


def test_align_sentences_falls_back_to_word_weighting():
    cards = media.align_sentences(["a b c", "d"], [(0.0, 4.0)])
    assert cards[0].end == 3.0 and cards[1].start == 3.0 and cards[1].end == 4.0


def test_chunk_cards_splits_on_word_count_and_punctuation():
    cards = media.chunk_cards([media.Card(0.0, 4.0, "Fix one. Start mid-sentence with energy")], max_words=3)
    assert [c.text for c in cards] == ["Fix one.", "Start mid-sentence with", "energy"]
    assert cards[0].start == 0.0 and abs(cards[-1].end - 4.0) < 0.01


def test_write_ass(tmp_path):
    out = tmp_path / "c.ass"
    media.write_ass([media.Card(0.0, 1.25, "hello {world}")], "The hook", out)
    text = out.read_text()
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
    assert "0:00:00.00,0:00:01.25,Caption" in text
    assert "(WORLD)" in text  # braces can't break ASS override tags
    assert "Hook,,0,0,0,," in text
