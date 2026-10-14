import numpy as np

from app.regions import box_region, contains, point_region, sample_lattice


SQUARE_WITH_HOLE = {
    "type": "Polygon",
    "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
    ],
}


def test_point_in_polygon_respects_holes():
    lats = np.array([1.0, 5.0, 11.0])
    lons = np.array([1.0, 5.0, 5.0])
    assert contains(SQUARE_WITH_HOLE, lats, lons).tolist() == [True, False, False]


def test_point_region_snaps_to_era5_grid():
    r = point_region(40.71, -74.01, "New York")
    assert (r.points[0].lat, r.points[0].lon) == (40.75, -74.0)
    assert r.key == "point:40.75,-74.00"


def test_lattice_respects_point_budget_and_grid():
    r = box_region(30, -100, 40, -90, None, max_points=9)
    assert 1 <= len(r.points) <= 9
    for p in r.points:
        assert (p.lat * 4) % 1 == 0 and (p.lon * 4) % 1 == 0  # on the 0.25 deg grid
        assert 30 <= p.lat <= 40 and -100 <= p.lon <= -90


def test_cos_latitude_weights():
    r = box_region(0, 0, 60, 10, None, max_points=9)
    w = {p.lat: p.weight for p in r.points}
    assert w[min(w)] > w[max(w)]


def test_tiny_region_falls_back_to_one_point():
    tiny = {"type": "Polygon", "coordinates": [[[0.01, 0.01], [0.05, 0.01], [0.05, 0.05], [0.01, 0.05], [0.01, 0.01]]]}
    pts, _ = sample_lattice(tiny, (0.01, 0.01, 0.05, 0.05), 9)
    assert len(pts) == 1
