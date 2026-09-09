"""The web app: folder grouping, error handling, and downloads."""

import io
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from antenna_audit.web.server import create_app

PREFIX = "Sector_{s}_RF_Antenna_Photos_Sector_{s}_RF_Antenna_Photos_Ant_Sec_{s}_"


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    app.extensions["job_store"].shutdown()


def photo_bytes(size=(1200, 900)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (80, 120, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def site_files(site: str, parent: str = "Parent"):
    """A minimal but complete-enough folder, named the way a browser sends it.

    Every part shares the field name "files", so they must be passed to the test
    client as a list under one key — a dict would keep only the last one.
    """
    prefix = PREFIX.format(s=1)
    names = [
        f"{prefix}_850_Tilt_1.jpg",
        f"{prefix}_1800_Tilt_1_1.jpg",
        f"{prefix}Antenna_M_Tilt_1.jpg",
        f"{prefix}Antenna_Azimuth_Photo_1.jpg",
    ]
    return [(io.BytesIO(photo_bytes()), f"{parent}/{site}/{n}") for n in names]


def wait_for(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/job/{job_id}").get_json()
        if data["state"] in ("done", "failed"):
            return data
        time.sleep(0.2)
    raise AssertionError("build did not finish")


def test_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Antenna Audit Photo Sheets" in response.data


def test_upload_splits_folders_into_separate_workbooks(client):
    """Two folders in one upload must produce two workbooks."""
    parts = site_files("SITEAAA") + site_files("SITEBBB")
    response = client.post(
        "/api/build", data={"files": parts, "maxDim": "2400"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    job = wait_for(client, response.get_json()["id"])

    assert job["total"] == 2
    assert job["readyCount"] == 2
    names = sorted(s["name"] for s in job["sites"])
    assert names == ["SITEAAA", "SITEBBB"]
    for site in job["sites"]:
        assert site["state"] == "done"
        assert site["filename"] == f"{site['name']} Antenna Audit Photos.xlsx"
        assert site["photosPlaced"] == 4
        assert site["problems"] == []


def test_each_workbook_downloads_and_the_zip_holds_them_all(client):
    parts = site_files("SITEAAA") + site_files("SITEBBB")
    job_id = client.post(
        "/api/build", data={"files": parts}, content_type="multipart/form-data"
    ).get_json()["id"]
    job = wait_for(client, job_id)

    for site in job["sites"]:
        one = client.get(f"/api/job/{job_id}/file/{site['filename']}")
        assert one.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(one.data)).namelist()

    bundle = client.get(f"/api/job/{job_id}/all")
    assert bundle.status_code == 200
    inside = zipfile.ZipFile(io.BytesIO(bundle.data)).namelist()
    assert sorted(inside) == [
        "SITEAAA Antenna Audit Photos.xlsx",
        "SITEBBB Antenna Audit Photos.xlsx",
    ]


def test_a_deeply_nested_upload_still_groups_by_the_photos_own_folder(client):
    """The site is the folder holding the photos, however deep the drop was."""
    job_id = client.post(
        "/api/build", data={"files": site_files("DEEPSITE", parent="A/B/C")},
        content_type="multipart/form-data",
    ).get_json()["id"]
    job = wait_for(client, job_id)
    assert [s["name"] for s in job["sites"]] == ["DEEPSITE"]


def test_folder_with_no_recognisable_photos_is_skipped_not_failed(client):
    job_id = client.post(
        "/api/build",
        data={"files": [(io.BytesIO(photo_bytes()), "Parent/ODDSITE/holiday.jpg")]},
        content_type="multipart/form-data",
    ).get_json()["id"]
    job = wait_for(client, job_id)
    site = job["sites"][0]
    assert site["state"] == "skipped"
    assert "not" in site["message"].lower() or "check" in site["message"].lower()


def test_local_build_reads_a_folder_in_place(client, tmp_path):
    site = tmp_path / "LOCALSITE"
    site.mkdir()
    prefix = PREFIX.format(s=2)
    for name in (f"{prefix}_900_Tilt_1.jpg", f"{prefix}Antenna_M_Tilt_1.jpg"):
        (site / name).write_bytes(photo_bytes())

    response = client.post("/api/build-local", json={"path": str(tmp_path)})
    assert response.status_code == 200
    job = wait_for(client, response.get_json()["id"])
    assert [s["name"] for s in job["sites"]] == ["LOCALSITE"]
    assert job["readyCount"] == 1


def test_bad_input_is_reported_not_crashed(client):
    assert client.post("/api/build-local", json={"path": ""}).status_code == 400
    assert client.post("/api/build-local", json={"path": "/no/such"}).status_code == 400
    assert client.post("/api/build", data={}).status_code == 400
    assert client.get("/api/job/doesnotexist").status_code == 404


def test_downloads_cannot_escape_the_job_folder(client, tmp_path):
    site = tmp_path / "SAFESITE"
    site.mkdir()
    prefix = PREFIX.format(s=1)
    (site / f"{prefix}_850_Tilt_1.jpg").write_bytes(photo_bytes())
    job_id = client.post(
        "/api/build-local", json={"path": str(tmp_path)}
    ).get_json()["id"]
    wait_for(client, job_id)

    for attack in ("../../../../etc/passwd", "....//....//etc/passwd"):
        assert client.get(f"/api/job/{job_id}/file/{attack}").status_code == 404


# --- Pre and Post together --------------------------------------------------

def _post_tree(root: Path, site: str, sectors=("S1", "S2")):
    """Post photos as they arrive: one folder per site, then per sector."""
    for sector in sectors:
        folder = root / site / sector
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (folder / f"p{i}.jpg").write_bytes(photo_bytes((900, 700)))
    return root


def test_progress_total_is_the_real_total_from_the_first_poll(client, tmp_path):
    """It used to count only the sites processed so far, so a 10-site run
    reported '2 of 2' while it was still working."""
    root = tmp_path / "pre"
    prefix = PREFIX.format(s=1)
    for site in ("SITE1", "SITE2", "SITE3"):
        folder = root / site
        folder.mkdir(parents=True)
        (folder / f"{prefix}_850_Tilt_1.jpg").write_bytes(photo_bytes())

    job_id = client.post("/api/build-local", json={"path": str(root)}).get_json()["id"]
    job = wait_for(client, job_id)
    assert job["total"] == 3


def test_a_post_folder_is_accepted_alongside_the_pre_folder(client, tmp_path):
    pre = tmp_path / "pre"
    site = pre / "SITEA"
    site.mkdir(parents=True)
    prefix = PREFIX.format(s=1)
    (site / f"{prefix}_850_Tilt_1.jpg").write_bytes(photo_bytes())

    post = _post_tree(tmp_path / "post", "SITEA")
    response = client.post("/api/build-local", json={
        "path": str(pre), "postPath": str(post),
    })
    assert response.status_code == 200
    job = wait_for(client, response.get_json()["id"])
    assert job["readyCount"] == 1
    # Post slots are reported even when the classifier defers them all.
    assert "postPlaced" in job["sites"][0]
    assert "postUndecided" in job["sites"][0]


def test_a_bad_post_path_is_rejected_clearly(client, tmp_path):
    pre = tmp_path / "pre"
    site = pre / "SITEA"
    site.mkdir(parents=True)
    (site / (PREFIX.format(s=1) + "_850_Tilt_1.jpg")).write_bytes(photo_bytes())

    response = client.post("/api/build-local", json={
        "path": str(pre), "postPath": "/no/such/post/folder",
    })
    assert response.status_code == 400
    assert "Not a folder" in response.get_json()["error"]


def test_a_post_folder_with_no_sector_folders_is_rejected(client, tmp_path):
    """Post photos are grouped by sector; a flat folder is the usual mistake."""
    pre = tmp_path / "pre"
    site = pre / "SITEA"
    site.mkdir(parents=True)
    (site / (PREFIX.format(s=1) + "_850_Tilt_1.jpg")).write_bytes(photo_bytes())

    flat = tmp_path / "flatpost"
    flat.mkdir()
    (flat / "loose.jpg").write_bytes(photo_bytes())

    response = client.post("/api/build-local", json={
        "path": str(pre), "postPath": str(flat),
    })
    assert response.status_code == 400
    assert "S1" in response.get_json()["error"]


def test_building_without_post_photos_still_works(client, tmp_path):
    """The Post folder is optional; omitting it gives blank drop boxes."""
    pre = tmp_path / "pre"
    site = pre / "SITEA"
    site.mkdir(parents=True)
    (site / (PREFIX.format(s=1) + "_850_Tilt_1.jpg")).write_bytes(photo_bytes())

    job_id = client.post("/api/build-local", json={"path": str(pre)}).get_json()["id"]
    job = wait_for(client, job_id)
    assert job["readyCount"] == 1
    assert job["sites"][0]["postPlaced"] == 0
