import json
import jsonpickle


class Test:

    def to_json(to_convert):
        #return json.dumps(to_convert, default=lambda o: o.__dict__, indent=2)
        return jsonpickle.encode(to_convert, indent=2)

    o = ["one", "two", "three"]
    o_json = to_json(o)

    print(o_json)
