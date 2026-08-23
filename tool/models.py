class GetchuGame:
    def __init__(self, date, name, company, size=None, link=None, nyaa_name=None, comment=None, downloaded=0, infohash_hex=None, submitted_115=0, submitted_pick_code=None):
        self.date = date
        self.year, self.month = map(int, date.split('-'))
        self.name = name
        self.company = company
        self.size = size
        self.link = link
        self.nyaa_name = nyaa_name
        self.comment = comment
        self.downloaded = downloaded
        self.infohash_hex = infohash_hex
        self.submitted_115 = submitted_115
        self.submitted_pick_code = submitted_pick_code

    def __str__(self):
        return f"GetchuGame(date={self.date}, name='{self.name}', company='{self.company}')"

    def __repr__(self):
        return self.__str__()


class NyaaData:
    def __init__(self, date, size, name, link):
        self.date = date
        self.size = size
        self.name = name
        self.link = link

    def __str__(self):
        return f"NyaaData(date={self.date}, size={self.size}, name='{self.name}')"

    def __repr__(self):
        return self.__str__()


class MatchResult:
    def __init__(
        self,
        date=None,
        name=None,
        company=None,
        selected_index=-1,
        matched_name=None,
        link=None,
        size=None,
        confidence=0.0,
        source="none",
        reason=None,
        candidate_count=0,
    ):
        self.date = date
        self.name = name
        self.company = company
        self.selected_index = selected_index
        self.matched_name = matched_name
        self.link = link
        self.size = size
        self.confidence = confidence
        self.source = source
        self.reason = reason
        self.candidate_count = candidate_count

    def has_match(self):
        return self.selected_index is not None and self.selected_index >= 0

    def __str__(self):
        return (
            f"MatchResult(date={self.date}, name='{self.name}', "
            f"selected_index={self.selected_index}, matched_name='{self.matched_name}', "
            f"confidence={self.confidence}, source='{self.source}')"
        )

    def __repr__(self):
        return self.__str__()
