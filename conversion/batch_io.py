"""Crash recovery for the converter's single-writer LeRobot dataset."""
import json
from pathlib import Path
import shutil


def json_write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def recover(output):
    """Roll back only files named in this converter's pending segment transaction."""
    transaction = output / ".r2-transaction"
    marker = transaction / "pending.json"
    if not marker.exists():
        if transaction.exists():
            # No mutation starts before the marker has been committed.
            shutil.rmtree(transaction)
        return False
    pending = json.loads(marker.read_text())
    for relative in pending["generated_files"]:
        path = (output / relative).resolve()
        if not path.is_relative_to(output.resolve()):
            raise ValueError("Invalid transaction path")
        path.unlink(missing_ok=True)
    images = output / "images"
    if images.exists():
        shutil.rmtree(images)
    # Restore metadata files atomically, retaining the snapshot until all succeed.
    before = transaction / "meta"
    for path in (output / "meta").iterdir():
        if path.is_file() and not (before / path.name).exists():
            path.unlink()
    for path in before.iterdir():
        replacement = output / "meta" / (path.name + ".recover")
        shutil.copyfile(path, replacement)
        replacement.replace(output / "meta" / path.name)
    for name, size in pending["ledger_sizes"].items():
        with (output / "provenance" / name).open("a+b") as stream:
            stream.truncate(size)
    marker.unlink()
    shutil.rmtree(transaction)
    return True


def begin(dataset):
    output = dataset.root
    transaction = output / ".r2-transaction"
    transaction.mkdir()
    shutil.copytree(output / "meta", transaction / "meta")
    index = dataset.meta.total_episodes
    generated = [str(dataset.meta.get_data_file_path(index))]
    generated += [str(dataset.meta.get_video_file_path(index, key)) for key in dataset.meta.video_keys]
    ledger_sizes = {name: (output / "provenance" / name).stat().st_size
                    if (output / "provenance" / name).exists() else 0
                    for name in ("frames.jsonl", "segments.jsonl")}
    json_write(transaction / "pending.json", {"generated_files": generated, "ledger_sizes": ledger_sizes})


def commit(output):
    # The closed segment ledger and official metadata are the commit point.
    # Remove the rollback marker atomically before garbage-collecting snapshots.
    (output / ".r2-transaction/pending.json").unlink()
    shutil.rmtree(output / ".r2-transaction")


def append_json(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def completed(output):
    path = output / "provenance/segments.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
