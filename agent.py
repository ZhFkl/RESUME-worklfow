"""终端入口，与网站共用 AgentCore 和持久存储。"""
import argparse
import asyncio
import json
import os
from dotenv import load_dotenv
from agent_core import AgentCore
from session_store import BASE_DIR, SessionStore
from task_manager import TaskManager


async def main(args):
    load_dotenv(BASE_DIR / ".env")
    store = SessionStore(args.data_dir)
    with store.runtime():
        await run_cli(args, store)


async def run_cli(args, store):
    profile_path = store.root / "cli-profile.json"
    if profile_path.exists():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        user_id = store.authenticate(profile["token"])
    else:
        profile = store.create_user()
        user_id = profile["user_id"]
    session_id = args.session or profile.get("session_id")
    if args.new_session or not session_id:
        session_id = store.create_session(user_id)["session_id"]
    store.history(user_id, session_id)
    profile["session_id"] = session_id
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    os.chmod(profile_path, 0o600)
    print(f"会话：{session_id}\n素材库：{store.workspace(user_id) / '项目经历库.md'}")
    manager = TaskManager(store, AgentCore(store))
    try:
        while True:
            try:
                message = (await asyncio.to_thread(input, "\nuser: ")).strip()
            except EOFError:
                break
            if message in {"exit", "quit"}:
                break
            if not message:
                continue
            task_id = manager.submit(user_id, session_id, message)
            shown = 0
            while True:
                result = store.task(user_id, session_id, task_id)
                for event in result["events"][shown:]:
                    print(f"[{event['type']}] {event.get('tool', '')}")
                shown = len(result["events"])
                if result["status"] in {"completed", "failed"}:
                    print("\nagent:", result["reply"] or result["error"])
                    break
                await asyncio.sleep(0.2)
    finally:
        await manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir")
    parser.add_argument("--session", help="继续指定的已有会话")
    parser.add_argument("--new-session", action="store_true")
    asyncio.run(main(parser.parse_args()))
