import importlib.util
import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr

HAS_FITZ = importlib.util.find_spec("fitz") is not None

DIM_TOL = 16.0


def _build_plan_pdf(
    scale_m_per_pt=0.02,
    angle_deg=0.0,
    with_dims=True,
    with_scale_bar=False,
):
    import fitz

    page_w, page_h = 1200, 900
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    cx, cy = page_w / 2, page_h / 2
    w_pt = 10 / scale_m_per_pt
    h_pt = 8 / scale_m_per_pt
    x0 = (page_w - w_pt) / 2
    y0 = (page_h - h_pt) / 2
    corners = [(x0, y0), (x0 + w_pt, y0), (x0 + w_pt, y0 + h_pt), (x0, y0 + h_pt)]
    if angle_deg:
        corners = [pr._rotate_xy(px, py, cx, cy, angle_deg) for (px, py) in corners]
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        page.draw_line(fitz.Point(*a), fitz.Point(*b), width=2, color=(0, 0, 0))
    if with_dims:
        page.draw_line(fitz.Point(x0, y0 - 40), fitz.Point(x0 + w_pt, y0 - 40), width=1, color=(0, 0, 0))
        page.draw_line(fitz.Point(x0, y0 - 40), fitz.Point(x0, y0 - 34), width=1, color=(0, 0, 0))
        page.draw_line(fitz.Point(x0 + w_pt, y0 - 40), fitz.Point(x0 + w_pt, y0 - 34), width=1, color=(0, 0, 0))
        page.draw_line(fitz.Point(x0 + w_pt + 40, y0), fitz.Point(x0 + w_pt + 40, y0 + h_pt), width=1, color=(0, 0, 0))
        page.draw_line(fitz.Point(x0 + w_pt + 40, y0), fitz.Point(x0 + w_pt + 46, y0), width=1, color=(0, 0, 0))
        page.draw_line(fitz.Point(x0 + w_pt + 40, y0 + h_pt), fitz.Point(x0 + w_pt + 46, y0 + h_pt), width=1, color=(0, 0, 0))
        page.insert_text(fitz.Point(x0 + 40, y0 - 44), "10m", fontsize=10)
        page.insert_text(fitz.Point(x0 + w_pt + 48, y0 + h_pt / 2 - 4), "8m", fontsize=10)
    if with_scale_bar:
        page.draw_line(fitz.Point(50, 800), fitz.Point(250, 800), width=2, color=(0, 0, 0))
        page.insert_text(fitz.Point(230, 788), "2", fontsize=10)
    tmp = Path(tempfile.mkdtemp())
    pdf_path = tmp / "plan.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def _page_dict(pdf_path):
    import fitz

    doc = fitz.open(pdf_path)
    page = doc[0]
    vec = pr.extract_page_vectors(page)
    doc.close()
    return vec


def _job_from_analysis(analysis, job_id="vectest"):
    return {
        "job_id": job_id,
        "analyses": [analysis],
        "rooms": [],
        "elevation_progress": {},
        "files": [],
    }


@unittest.skipUnless(HAS_FITZ, "PyMuPDF required")
class PdfVectorGeometryTests(unittest.TestCase):
    def test_extract_page_vectors_lines_and_size(self):
        pdf = _build_plan_pdf()
        vec = _page_dict(pdf)
        self.assertEqual(vec["page_w_pt"], 1200.0)
        self.assertEqual(vec["page_h_pt"], 900.0)
        self.assertGreaterEqual(len(vec["lines"]), 4)

    def test_detect_dimension_texts(self):
        pdf = _build_plan_pdf()
        vec = _page_dict(pdf)
        dims = pr.detect_dimension_texts(vec["words"])
        values = sorted(d["value_m"] for d in dims)
        self.assertIn(10.0, values)
        self.assertIn(8.0, values)

    def test_parse_dimension_value_units(self):
        self.assertAlmostEqual(pr.parse_dimension_value("10m"), 10.0)
        self.assertAlmostEqual(pr.parse_dimension_value("3,500"), 3.5)
        self.assertAlmostEqual(pr.parse_dimension_value("3500"), 3.5)
        self.assertAlmostEqual(pr.parse_dimension_value("2500mm"), 2.5)
        self.assertAlmostEqual(pr.parse_dimension_value("4.8"), 4.8)
        self.assertIsNone(pr.parse_dimension_value("Lounge"))
        self.assertIsNone(pr.parse_dimension_value("12.5m2"))
        self.assertIsNone(pr.parse_dimension_value(""))

    def test_solve_vector_scale_from_dimension_text(self):
        pdf = _build_plan_pdf(scale_m_per_pt=0.02)
        vec = _page_dict(pdf)
        dims = pr.detect_dimension_texts(vec["words"])
        scale = pr.solve_vector_scale(vec["lines"], dims, vec["page_w_pt"], vec["page_h_pt"])
        self.assertIsNotNone(scale)
        self.assertEqual(scale["source"], "dimension-text")
        self.assertGreaterEqual(scale["dims_used"], 2)
        self.assertAlmostEqual(scale["m_per_pt"], 0.02, places=5)

    def test_solve_vector_scale_from_scale_bar(self):
        pdf = _build_plan_pdf(with_dims=False, with_scale_bar=True)
        vec = _page_dict(pdf)
        dims = pr.detect_dimension_texts(vec["words"])
        scale = pr.solve_vector_scale(vec["lines"], dims, vec["page_w_pt"], vec["page_h_pt"])
        self.assertIsNotNone(scale)
        self.assertEqual(scale["source"], "scale-bar")
        self.assertAlmostEqual(scale["m_per_pt"], 0.01, places=5)

    def test_solve_vector_scale_none_when_no_dims(self):
        pdf = _build_plan_pdf(with_dims=False)
        vec = _page_dict(pdf)
        scale = pr.solve_vector_scale(vec["lines"], [], vec["page_w_pt"], vec["page_h_pt"])
        self.assertIsNone(scale)

    def test_estimate_plan_rotation(self):
        pdf = _build_plan_pdf(angle_deg=2.0, with_dims=False)
        vec = _page_dict(pdf)
        angle = pr.estimate_plan_rotation(vec["lines"])
        self.assertAlmostEqual(angle, 2.0, delta=0.5)

    def test_vector_envelope_perimeter_deskewed(self):
        pdf = _build_plan_pdf(angle_deg=2.0, with_dims=False)
        vec = _page_dict(pdf)
        walls = pr.building_wall_lines(vec["lines"], [])
        env = pr.vector_envelope_perimeter(walls, 0.02, angle_deg=2.0, page_w_pt=1200.0, page_h_pt=900.0)
        self.assertIsNotNone(env)
        self.assertEqual(env["method"], "vector-wall")
        self.assertAlmostEqual(env["perimeter_m"], 36.0, delta=0.3)
        skewed = pr.vector_envelope_perimeter(walls, 0.02, angle_deg=0.0, page_w_pt=1200.0, page_h_pt=900.0)
        self.assertGreater(skewed["perimeter_m"], 36.5)

    def test_building_wall_lines_excludes_dimension_lines(self):
        pdf = _build_plan_pdf()
        vec = _page_dict(pdf)
        dims = pr.detect_dimension_texts(vec["words"])
        walls = pr.building_wall_lines(vec["lines"], dims, page_w_pt=1200.0, page_h_pt=900.0)
        lens = sorted(round(pr._seg_len_pt(*l), 2) for l in walls)
        self.assertEqual(lens, [400.0, 400.0, 500.0, 500.0])
        env = pr.vector_envelope_perimeter(walls, 0.02, page_w_pt=1200.0, page_h_pt=900.0)
        self.assertAlmostEqual(env["envelope_w_m"], 10.0, delta=0.1)
        self.assertAlmostEqual(env["envelope_h_m"], 8.0, delta=0.1)


@unittest.skipUnless(HAS_FITZ, "PyMuPDF required")
class PdfVectorIntegrationTests(unittest.TestCase):
    def test_analyse_pdf_stores_vector_scale(self):
        pdf = _build_plan_pdf()
        analysis = pr.analyse_pdf(pdf, render_pages=True, dpi=72)
        page = analysis["pages"][0]
        vs = page["vector_scale"]
        self.assertAlmostEqual(vs["m_per_pt"], 0.02, places=5)
        self.assertEqual(vs["source"], "dimension-text")
        self.assertGreaterEqual(vs["wall_line_count"], 4)
        self.assertGreaterEqual(len(page["vector_wall_lines"]), 4)
        self.assertEqual(page["render_dpi"], 72)
        self.assertTrue(Path(page["image_path"]).exists())
        self.assertGreaterEqual(len(analysis["converted_images"]), 1)

    def test_plan_auto_scale_m_per_px(self):
        pdf = _build_plan_pdf()
        analysis = pr.analyse_pdf(pdf, render_pages=True, dpi=72)
        job = _job_from_analysis(analysis)
        auto = pr.plan_auto_scale(job, "plan.pdf", 1, dpi=72)
        self.assertIsNotNone(auto)
        self.assertAlmostEqual(auto["m_per_px"], 0.02, places=6)
        self.assertAlmostEqual(auto["m_per_pt"], 0.02, places=6)

    def test_plan_auto_scale_none_for_unknown_page(self):
        pdf = _build_plan_pdf()
        analysis = pr.analyse_pdf(pdf, render_pages=True, dpi=72)
        job = _job_from_analysis(analysis)
        self.assertIsNone(pr.plan_auto_scale(job, "plan.pdf", 99, dpi=72))
        self.assertIsNone(pr.plan_auto_scale(job, "missing.pdf", 1, dpi=72))

    def test_external_footprint_vector_wall(self):
        pdf = _build_plan_pdf()
        analysis = pr.analyse_pdf(pdf, render_pages=False, dpi=72)
        job = _job_from_analysis(analysis)
        fp = pr.external_footprint(job, [], [])
        self.assertEqual(fp["method"], "vector-wall")
        self.assertAlmostEqual(fp["perimeter_m"], 36.0, delta=0.3)
        self.assertAlmostEqual(fp["envelope_w_m"], 10.0, delta=0.1)
        self.assertAlmostEqual(fp["envelope_h_m"], 8.0, delta=0.1)
        self.assertIn("wall geometry", fp["note"])

    def test_compute_external_rows_uses_vector_footprint(self):
        pdf = _build_plan_pdf()
        analysis = pr.analyse_pdf(pdf, render_pages=False, dpi=72)
        job = _job_from_analysis(analysis)
        rows, info = pr.compute_external_takeoff_rows(job)
        self.assertEqual(info["method"], "vector-wall")
        self.assertAlmostEqual(info["perimeter_m"], 36.0, delta=0.3)
        self.assertAlmostEqual(info["gross_walls_m2"], 97.2, delta=0.8)  # 36 x 2.7
        self.assertAlmostEqual(info["soffits_m2"], 16.2, delta=0.2)  # 36 x 0.45
        by_substrate = {r["substrate"]: r for r in rows}
        self.assertIn("External walls / render", by_substrate)
        self.assertEqual(by_substrate["Fascia / gutters / trim"]["lineal_m"], info["perimeter_m"])


if __name__ == "__main__":
    unittest.main()
