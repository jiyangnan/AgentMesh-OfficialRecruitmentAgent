"""Local migration reads only selected account facts and never uploads them."""
import json
import os
import sqlite3

import pytest

from official_recruitment_agent.local_materials_export import LocalMaterialsExport
from official_recruitment_agent.local_profile_handoff import LocalProfileStore


@pytest.fixture
def local(tmp_path):
    path=tmp_path/"local.sqlite";LocalProfileStore(path)
    with sqlite3.connect(path) as connection:
        for ref,workspace,value in [("localfact_one","ws_a","SYNTHETIC selected"),("localfact_two","ws_a","SYNTHETIC unselected"),("localfact_other","ws_b","SYNTHETIC other account")]:
            connection.execute("INSERT INTO local_profile_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(ref,workspace,ref,ref,value,"account","","standard","[]","pq_one",None,None,"2026-09-20T00:00:00+00:00","2026-09-25T00:00:00+00:00"))
    return LocalMaterialsExport(path,workspace_ref="ws_a")


def test_list_contains_metadata_only_and_export_requires_explicit_selection(local,tmp_path):
    assert len(local.list())==2 and "SYNTHETIC selected" not in json.dumps(local.list())
    output=tmp_path/"chosen.json"
    local.export(["localfact_one"],output=output,device_label="SYNTHETIC laptop",content_locale="ja")
    body=json.loads(output.read_text());assert len(body["items"])==1
    assert body["items"][0]["first_saved_at"]=="2026-09-20T00:00:00+00:00"
    assert body["items"][0]["content"]["value"]=="SYNTHETIC selected"
    assert "SYNTHETIC unselected" not in output.read_text() and str(local.path) not in output.read_text()
    assert os.stat(output).st_mode&0o777==0o600
    assert len(local.list())==2
    with pytest.raises(ValueError):local.export([],output=tmp_path/"empty.json",device_label="SYNTHETIC",content_locale="en")
    with pytest.raises(ValueError):local.export(["localfact_other"],output=tmp_path/"other.json",device_label="SYNTHETIC",content_locale="en")
    with pytest.raises(FileExistsError):local.export(["localfact_one"],output=output,device_label="SYNTHETIC",content_locale="en")


def test_missing_question_and_history_remain_unknown_without_guessing(local,tmp_path):
    path=tmp_path/"selected.json"
    local.export(["localfact_one"],output=path,device_label="SYNTHETIC",content_locale="ko")
    item=json.loads(path.read_text())["items"][0]
    assert item["content"]["question"]=="" and item["action"]=="unresolved"
    assert "answer_context" not in item


def test_missing_store_is_not_created(tmp_path):
    path=tmp_path/"absent.sqlite"
    assert LocalMaterialsExport(path,workspace_ref="ws_a").list()==[]
    assert not path.exists()
