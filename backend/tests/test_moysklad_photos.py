"""Перенос фото из МойСклад: id изображения, отбор новых, имя файла, уменьшение."""
import os
import tempfile

from app.services.moysklad_photos import image_id, new_images, safe_name, shrink


IMG = lambda i: {"meta": {"href": f"https://api.moysklad.ru/api/remap/1.2/entity/product/p1/images/{i}",   # noqa: E731
                          "downloadHref": f"https://api.moysklad.ru/api/remap/1.2/download/{i}"}, "filename": f"{i}.jpg"}


def test_image_id_and_new():
    assert image_id(IMG("abc")) == "abc"
    have = [{"ms_image_id": "a1"}]
    assert [image_id(x) for x in new_images(have, [IMG("a1"), IMG("b2"), {"meta": {"href": "x/c3"}}])] == ["b2"]


def test_safe_name():
    assert safe_name("фото/1:2?.jpg") == "фото_1_2_.jpg"
    assert safe_name("") == "photo.jpg"


def test_shrink():
    from PIL import Image
    path = os.path.join(tempfile.mkdtemp(), "big.jpg")
    Image.new("RGB", (3200, 1600), "red").save(path)
    shrink(path)
    with Image.open(path) as im:
        assert im.size == (1600, 800)
