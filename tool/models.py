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
