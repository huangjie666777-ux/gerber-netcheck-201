
class GerberError(Exception):
    """Gerber 解析/构建错误，携带原文位置。"""

    def __init__(self, message, line=None, source=None):
        self.message = message
        self.line = line
        self.source = source
        super().__init__(self.__str__())

    def __str__(self):
        loc = ""
        if self.line is not None:
            loc = f"line {self.line}"
            if self.source:
                loc += f" [{self.source.strip()}]"
            loc += ": "
        return loc + self.message


class NetlistError(Exception):
    """网表 JSON 解析/校验错误，携带条目位置。"""

    def __init__(self, message, location=None):
        self.message = message
        self.location = location
        super().__init__(self.__str__())

    def __str__(self):
        if self.location:
            return "%s: %s" % (self.location, self.message)
        return self.message


class NetcheckError(Exception):
    """连通分析阶段的错误(如端子落铜歧义)。"""

    def __init__(self, message):
        self.message = message
        super().__init__(message)
