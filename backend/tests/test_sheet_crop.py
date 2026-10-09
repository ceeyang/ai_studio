"""三视图设定图自动切图：纯色背景上按列投影找出每个人物，切成单个视角。"""
from PIL import Image, ImageDraw, ImageFilter

from backend.domain.sheet_crop import crop_boxes, crop_sheet

BG = (246, 246, 246)


def sheet(xs: list[tuple[int, int]], *, w=1792, h=768, bg=BG, top=40, bottom=730,
          shadow=False, noise=False) -> Image.Image:
    """在纯色背景上画几个"人"：头（圆）+ 身体（矩形）+ 两条腿，xs 是每个人身体的左右边。"""
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    for x0, x1 in xs:
        cx = (x0 + x1) // 2
        head = (x1 - x0) // 3
        d.ellipse([cx - head // 2, top, cx + head // 2, top + head], fill=(60, 45, 40))
        d.rectangle([x0, top + head, x1, top + 380], fill=(25, 35, 70))
        d.rectangle([x0 + 8, top + 380, cx - 6, bottom], fill=(28, 38, 75))
        d.rectangle([cx + 6, top + 380, x1 - 8, bottom], fill=(28, 38, 75))
        if shadow:  # 脚下的淡影子，比背景略深
            d.ellipse([x0 - 30, bottom - 6, x1 + 30, bottom + 14], fill=(228, 228, 228))
    if noise:
        im = im.filter(ImageFilter.GaussianBlur(1.2))
    return im


FOUR = [(120, 300), (560, 720), (1000, 1120), (1450, 1640)]


def test_four_figures_found_in_order():
    boxes, method = crop_boxes(sheet(FOUR), 4)
    assert method == "segment"
    assert len(boxes) == 4
    for (x0, y0, x1, y1), (fx0, fx1) in zip(boxes, FOUR):
        assert x0 <= fx0 - 4 and x1 >= fx1 + 4          # 左右留了边
        assert x0 >= fx0 - 160 and x1 <= fx1 + 160      # 但没吃进别人
        assert y0 <= 40 and y1 >= 730                    # 头顶到脚都在
    # 同一高度：全身视角要同一比例
    assert len({(b[1], b[3]) for b in boxes}) == 1
    # 相邻两格不重叠
    for a, b in zip(boxes, boxes[1:]):
        assert a[2] <= b[0]


def test_crops_do_not_include_neighbours():
    im = sheet(FOUR)
    for crop in crop_sheet(im, 4)[0]:
        px = crop.load()
        w, h = crop.size
        # 左右边缘一列都是背景
        assert all(px[0, y] == BG for y in range(h)) and all(px[w - 1, y] == BG for y in range(h))


def test_soft_shadow_and_blur_still_segment():
    boxes, method = crop_boxes(sheet(FOUR, shadow=True, noise=True), 4)
    assert method == "segment" and len(boxes) == 4


def test_light_grey_background_detected_from_border():
    boxes, method = crop_boxes(sheet(FOUR, bg=(205, 208, 210)), 4)
    assert method == "segment" and len(boxes) == 4


def test_fragmented_figure_is_merged():
    # 第二个人中间断开（比如手臂和身体之间的空隙）：窄缝要并回同一个人
    im = sheet(FOUR)
    ImageDraw.Draw(im).rectangle([636, 0, 642, 768], fill=BG)
    boxes, method = crop_boxes(im, 4)
    assert method == "segment" and len(boxes) == 4
    assert boxes[1][0] <= 556 and boxes[1][2] >= 724


def test_three_heads():
    heads = [(200, 520), (740, 1060), (1280, 1600)]
    boxes, method = crop_boxes(sheet(heads, w=1792, h=768), 3)
    assert method == "segment" and len(boxes) == 3


def test_wrong_count_falls_back_to_equal_columns():
    five = [(60, 200), (420, 560), (780, 920), (1140, 1280), (1500, 1640)]
    boxes, method = crop_boxes(sheet(five), 4)
    assert method == "equal"
    assert boxes == [(0, 0, 448, 768), (448, 0, 896, 768), (896, 0, 1344, 768), (1344, 0, 1792, 768)]


def test_busy_background_falls_back_to_equal_columns():
    im = Image.new("RGB", (1200, 600), (240, 240, 240))
    d = ImageDraw.Draw(im)
    for x in range(0, 1200, 20):   # 满屏竖条纹：没有干净的背景
        d.rectangle([x, 0, x + 9, 600], fill=(30, 30, 30))
    boxes, method = crop_boxes(im, 3)
    assert method == "equal" and len(boxes) == 3


def test_forced_equal():
    boxes, method = crop_boxes(sheet(FOUR), 4, method="equal")
    assert method == "equal" and boxes[0] == (0, 0, 448, 768)


def test_floor_shadow_and_touching_shoulders_do_not_merge_figures():
    # 地面有一道连着两个人的深色影子、头像下面的肩膀几乎挨着：只要上半部分分得开就按人物切
    im = sheet(FOUR)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 700, 1792, 768], fill=(150, 150, 155))          # 深色地面横贯全图
    d.rectangle([300, 600, 1000, 700], fill=(40, 40, 60))            # 第 1、2 个人下半身之间的东西
    boxes, method = crop_boxes(im, 4)
    assert method == "segment" and len(boxes) == 4
    assert boxes[0][2] <= 560 + 1 and boxes[1][0] >= 300


def test_figure_touching_side_edge():
    heads = [(0, 380), (700, 1060), (1400, 1792)]
    boxes, method = crop_boxes(sheet(heads), 3)
    assert method == "segment" and boxes[0][0] == 0 and boxes[2][2] == 1792
