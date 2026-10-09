"""Explicit, read-only selection from this product's existing local adapter."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager


class LocalMaterialsExport:
    def __init__(self,path,*,workspace_ref):
        self.path,self.workspace=Path(path).expanduser(),workspace_ref

    @contextmanager
    def _connect(self):
        connection=sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True)
        connection.row_factory=sqlite3.Row
        try:yield connection
        finally:connection.close()

    def list(self):
        if not self.path.exists():return []
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT fact_id,label,scope,scope_ref,privacy,created_at FROM local_profile_facts WHERE workspace_ref=? ORDER BY created_at,fact_id",
                (self.workspace,))]

    def export(self, fact_ids, *, output, device_label, content_locale):
        if (not fact_ids or len(fact_ids)>50 or len(set(fact_ids))!=len(fact_ids) or content_locale not in {"zh-CN","en","ja","ko"}
            or not device_label.strip() or len(device_label)>100 or any(c in device_label for c in ("/","\\","\x00"))):
            raise ValueError("local_selection_invalid")
        items=[]
        with self._connect() as connection:
            for ref in fact_ids:
                row=connection.execute("SELECT * FROM local_profile_facts WHERE fact_id=? AND workspace_ref=?",(ref,self.workspace)).fetchone()
                if row is None:raise ValueError("local_selected_fact_missing")
                question=connection.execute("""SELECT json_extract(q.value,'$.site_label') FROM profile_handoff_proposals p,
                    json_each(p.questions_json) q WHERE p.workspace_ref=? AND p.status='confirmed'
                    AND json_extract(q.value,'$.question_id')=? ORDER BY p.confirmed_at DESC LIMIT 1""",(self.workspace,row["source_question_id"])).fetchone()
                fact={"key":row["canonical_key"],"label":row["label"],"value":row["value"],"scope":row["scope"],
                    "scope_ref":row["scope_ref"] or None,"privacy":row["privacy"],"aliases":json.loads(row["aliases_json"]),
                    "question":question[0] if question and question[0] else ""}
                conditional=fact["key"] in {"work_authorization","visa","visa_status","sponsorship"}
                items.append({"item_id":ref,"source_id":ref,"kind":"fact","name":row["label"],"content_locale":content_locale,
                    "first_saved_at":row["created_at"] or None,"content":fact,"action":"unresolved",
                    "target":"conditional_answer" if conditional else "profile"})
        payload={"contract_version":"official-local-materials-v1","workspace_ref":self.workspace,"device_label":device_label,"items":items}
        # Never overwrite an existing file or follow a destination symlink.
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0)
        descriptor=os.open(output,flags,0o600)
        with os.fdopen(descriptor,"w",encoding="utf-8") as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2);stream.write("\n")
        return {"exported_count":len(items),"contract_version":payload["contract_version"],"uploaded":False,"local_originals_unchanged":True}
