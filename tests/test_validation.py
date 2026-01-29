# -*- coding: utf-8 -*-
import os
import pytest
import numpy as np
from confflow.core import validation

def test_validate_positive_success():
    validation.validate_positive(10, "test_param")
    validation.validate_positive("2.5", "test_param")

def test_validate_positive_fail():
    with pytest.raises(validation.ValidationError, match="必须为正数"):
        validation.validate_positive(0, "test_param")
    with pytest.raises(validation.ValidationError, match="必须为正数"):
        validation.validate_positive(-1, "test_param")
    with pytest.raises(validation.ValidationError, match="必须为数值类型"):
        validation.validate_positive("abc", "test_param")

def test_validate_non_negative_success():
    validation.validate_non_negative(0, "test_param")
    validation.validate_non_negative(10, "test_param")

def test_validate_non_negative_fail():
    with pytest.raises(validation.ValidationError, match="必须为非负数"):
        validation.validate_non_negative(-0.1, "test_param")

def test_validate_integer_success():
    assert validation.validate_integer(10, "p") == 10
    assert validation.validate_integer("5", "p") == 5

def test_validate_integer_range():
    with pytest.raises(validation.ValidationError, match="必须 >= 1"):
        validation.validate_integer(0, "p", min_val=1)
    with pytest.raises(validation.ValidationError, match="必须 <= 10"):
        validation.validate_integer(11, "p", max_val=10)

def test_validate_float_range():
    assert validation.validate_float_range(0.5, "p", min_val=0.0, max_val=1.0) == 0.5
    with pytest.raises(validation.ValidationError):
        validation.validate_float_range(1.5, "p", max_val=1.0)

def test_validate_not_empty():
    validation.validate_not_empty([1], "p")
    validation.validate_not_empty("s", "p")
    with pytest.raises(validation.ValidationError, match="不能为 None"):
        validation.validate_not_empty(None, "p")
    with pytest.raises(validation.ValidationError, match="不能为空"):
        validation.validate_not_empty([], "p")
    with pytest.raises(validation.ValidationError, match="不能为空"):
        validation.validate_not_empty("", "p")

def test_validate_file_exists(tmp_path):
    f = tmp_path / "test.txt"
    f.touch()
    validation.validate_file_exists(str(f), "file")
    
    with pytest.raises(validation.ValidationError, match="文件不存在"):
        validation.validate_file_exists(str(tmp_path / "missing.txt"), "file")
    with pytest.raises(validation.ValidationError, match="文件路径不能为空"):
        validation.validate_file_exists("", "file")
