class GetchuGame:
    def __init__(self, date, name, company):
        self.date = date
        self.name = name
        self.company = company

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
        return f"NyaaData(date={self.date}, size={self.size}, name='{self.name}', link='{self.link}')"
    
    def __repr__(self):
        return self.__str__()