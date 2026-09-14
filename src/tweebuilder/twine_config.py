from pydantic import BaseModel


class ActConfig(BaseModel):
    name: str
    file_id: str


class TwineConfig(BaseModel):
    acts: list[ActConfig] = [
        ActConfig(
            name="Act 2 outline",
            file_id="1spIIxfgv-KMgcOhvWIcqZDp24UTp0QV_8pwpIOIWN6o",
        )
    ]
    watch_list: list[str] = [
        "1p_IgOS4YUiurCriktVdz652LvyTUa-gO_FtpnMCuh6Q",
        "1spIIxfgv-KMgcOhvWIcqZDp24UTp0QV_8pwpIOIWN6o",
    ]


global_twine_config = TwineConfig()
