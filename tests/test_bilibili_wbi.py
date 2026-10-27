import hashlib
import importlib.util
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "skills" / "bilibili-web" / "entry.py"
spec = importlib.util.spec_from_file_location("bilibili_web_entry", ENTRY)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

IMG = "7cd084941338484aae1ad9425b84077c"
SUB = "4932caff0ff746eab6f01bf08b70ac45"
MIXIN = "ea1db124af3c7062474693fa704f4ff8"


def test_mixin_key_known_vector():
    assert module.mixin_key(IMG + SUB) == MIXIN


def test_sign_wbi_known_vector():
    query = module.sign_wbi({"foo": "114", "bar": "514", "zab": 1919810}, IMG, SUB, wts=1702204169)
    base = "bar=514&foo=114&wts=1702204169&zab=1919810"
    digest = hashlib.md5((base + MIXIN).encode()).hexdigest()
    assert digest == "8f6f2b5b3d485fe1886cec6a0be8c5d4"
    assert query == f"{base}&w_rid={digest}"


def test_encode_query_uses_percent20_and_uppercase():
    encoded = module.encode_query({"foo": "one one", "bar": "五"})
    assert encoded == "bar=%E4%BA%94&foo=one%20one"
