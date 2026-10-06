from datetime import date

from application.use_cases.baseline_source import BaselineSource
from domain.baseline import ArticleFacts, ChannelFacts

EMPTY = ChannelFacts(0, 0, 0, 0, 0, 0)


class FakeReader:
    def __init__(self):
        self.asked = None

    def read(self, as_of):
        self.asked = as_of
        return [ArticleFacts("PT1/6-17-17/1", "постельное", ChannelFacts(7, 30, 30, 0, 0, 0), EMPTY, 0, 0, 0, 0)]

    def quants(self):
        return {"/6-17-17/": 8.0}


def test_source_calculates_with_the_quant_of_the_size_key():
    reader = FakeReader()
    rows = BaselineSource(reader, as_of_provider=lambda: date(2026, 10, 7)).fetch("all")
    assert reader.asked == date(2026, 10, 7)
    assert [r.article for r in rows] == ["PT1/6-17-17/1"]
    assert rows[0].inputs.quant == 8.0
    assert rows[0].result.need == 32  # 30 pieces rounded up to the quant of 8
