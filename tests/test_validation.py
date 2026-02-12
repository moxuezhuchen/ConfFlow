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


def test_validate_file_exists_not_a_file(tmp_path):
    """Test validation fails when path is not a file"""
    with pytest.raises(validation.ValidationError, match="路径不是文件"):
        validation.validate_file_exists(str(tmp_path), "file")


def test_validate_dir_exists_success(tmp_path):
    """Test directory exists validation"""
    validation.validate_dir_exists(str(tmp_path), "dir")


def test_validate_dir_exists_missing(tmp_path):
    """Test validation fails for missing directory"""
    with pytest.raises(validation.ValidationError, match="目录不存在"):
        validation.validate_dir_exists(str(tmp_path / "missing"), "dir")


def test_validate_dir_exists_empty_path():
    """Test validation fails for empty directory path"""
    with pytest.raises(validation.ValidationError, match="目录路径不能为空"):
        validation.validate_dir_exists("", "dir")


def test_validate_dir_exists_not_a_dir(tmp_path):
    """Test validation fails when path is not a directory"""
    f = tmp_path / "file.txt"
    f.touch()
    with pytest.raises(validation.ValidationError, match="路径不是目录"):
        validation.validate_dir_exists(str(f), "dir")


def test_validate_coords_array_success():
    """Test coordinate array validation"""
    coords = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    result = validation.validate_coords_array(coords, "coords")
    assert result.shape == (2, 3)


def test_validate_coords_array_with_expected_atoms():
    """Test coordinate array validation with expected atom count"""
    coords = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    result = validation.validate_coords_array(coords, "coords", expected_atoms=2)
    assert result.shape == (2, 3)


def test_validate_coords_array_wrong_atom_count():
    """Test validation fails for wrong atom count"""
    coords = [[0.0, 0.0, 0.0]]
    with pytest.raises(validation.ValidationError, match="原子数不匹配"):
        validation.validate_coords_array(coords, "coords", expected_atoms=5)


def test_validate_coords_array_none():
    """Test validation fails for None coords"""
    with pytest.raises(validation.ValidationError, match="坐标不能为 None"):
        validation.validate_coords_array(None, "coords")


def test_validate_coords_array_not_2d():
    """Test validation fails for non-2D array"""
    coords = [0.0, 0.0, 0.0]  # 1D
    with pytest.raises(validation.ValidationError, match="坐标必须是二维数组"):
        validation.validate_coords_array(coords, "coords")


def test_validate_coords_array_wrong_shape():
    """Test validation fails for wrong shape"""
    coords = [[0.0, 0.0]]  # (N, 2) instead of (N, 3)
    with pytest.raises(validation.ValidationError, match="坐标必须是 \\(N, 3\\) 形状"):
        validation.validate_coords_array(coords, "coords")


def test_validate_coords_array_with_nan():
    """Test validation fails for NaN values"""
    coords = [[0.0, 0.0, float('nan')]]
    with pytest.raises(validation.ValidationError, match="坐标包含 NaN"):
        validation.validate_coords_array(coords, "coords")


def test_validate_coords_array_with_inf():
    """Test validation fails for Inf values"""
    coords = [[0.0, 0.0, float('inf')]]
    with pytest.raises(validation.ValidationError, match="坐标包含无穷大"):
        validation.validate_coords_array(coords, "coords")


def test_validate_coords_array_non_numeric():
    """Test validation fails for non-numeric values"""
    coords = [["a", "b", "c"]]
    with pytest.raises(validation.ValidationError, match="无法转换为数值数组"):
        validation.validate_coords_array(coords, "coords")


def test_validate_atom_indices_success():
    """Test atom indices validation"""
    validation.validate_atom_indices([1, 2, 3], "indices", max_index=5)


def test_validate_atom_indices_empty():
    """Test empty indices list is valid"""
    validation.validate_atom_indices([], "indices", max_index=5)


def test_validate_atom_indices_not_integer():
    """Test validation fails for non-integer indices"""
    with pytest.raises(validation.ValidationError, match="不是整数"):
        validation.validate_atom_indices([1, 2.5, 3], "indices", max_index=5)


def test_validate_atom_indices_below_one():
    """Test validation fails for indices < 1"""
    with pytest.raises(validation.ValidationError, match="原子索引必须 >= 1"):
        validation.validate_atom_indices([0, 1, 2], "indices", max_index=5)


def test_validate_atom_indices_exceeds_max():
    """Test validation fails for indices > max"""
    with pytest.raises(validation.ValidationError, match="原子索引.*超出范围"):
        validation.validate_atom_indices([1, 2, 10], "indices", max_index=5)


def test_validate_bond_pair_success():
    """Test bond pair validation"""
    result = validation.validate_bond_pair([1, 2], "bond", max_index=5)
    assert result == (1, 2)


def test_validate_bond_pair_tuple():
    """Test bond pair validation with tuple"""
    result = validation.validate_bond_pair((3, 4), "bond", max_index=5)
    assert result == (3, 4)


def test_validate_bond_pair_wrong_length():
    """Test validation fails for wrong length"""
    with pytest.raises(validation.ValidationError, match="键对必须是长度为 2"):
        validation.validate_bond_pair([1, 2, 3], "bond", max_index=5)


def test_validate_bond_pair_not_list_or_tuple():
    """Test validation fails for wrong type"""
    with pytest.raises(validation.ValidationError, match="键对必须是长度为 2"):
        validation.validate_bond_pair("12", "bond", max_index=5)


def test_validate_bond_pair_non_integer():
    """Test validation fails for non-integer elements"""
    with pytest.raises(validation.ValidationError, match="键对元素必须是整数"):
        validation.validate_bond_pair([1, "a"], "bond", max_index=5)


def test_validate_bond_pair_below_one():
    """Test validation fails for indices < 1"""
    with pytest.raises(validation.ValidationError, match="原子索引必须 >= 1"):
        validation.validate_bond_pair([0, 1], "bond", max_index=5)


def test_validate_bond_pair_exceeds_max():
    """Test validation fails for indices > max"""
    with pytest.raises(validation.ValidationError, match="原子索引超出范围"):
        validation.validate_bond_pair([1, 10], "bond", max_index=5)


def test_validate_bond_pair_same_atom():
    """Test validation fails for same atom"""
    with pytest.raises(validation.ValidationError, match="键对不能是同一个原子"):
        validation.validate_bond_pair([2, 2], "bond", max_index=5)


def test_validate_choice_success():
    """Test choice validation"""
    validation.validate_choice("a", "choice", ["a", "b", "c"])


def test_validate_choice_fail():
    """Test validation fails for invalid choice"""
    with pytest.raises(validation.ValidationError, match="必须是以下选项之一"):
        validation.validate_choice("d", "choice", ["a", "b", "c"])


def test_validate_string_not_empty_success():
    """Test string validation"""
    result = validation.validate_string_not_empty("  hello  ", "str")
    assert result == "hello"


def test_validate_string_not_empty_none():
    """Test validation fails for None"""
    with pytest.raises(validation.ValidationError, match="不能为 None"):
        validation.validate_string_not_empty(None, "str")


def test_validate_string_not_empty_not_string():
    """Test validation fails for non-string"""
    with pytest.raises(validation.ValidationError, match="必须是字符串类型"):
        validation.validate_string_not_empty(123, "str")


def test_validate_string_not_empty_whitespace():
    """Test validation fails for whitespace-only string"""
    with pytest.raises(validation.ValidationError, match="不能为空字符串"):
        validation.validate_string_not_empty("   ", "str")


def test_validate_params_decorator():
    """Test validate_params decorator"""
    @validation.validate_params(
        threshold=lambda v, n: validation.validate_positive(v, n),
    )
    def my_func(threshold):
        return threshold * 2
    
    result = my_func(threshold=5)
    assert result == 10


def test_validate_params_decorator_fail():
    """Test validate_params decorator raises on invalid"""
    @validation.validate_params(
        threshold=lambda v, n: validation.validate_positive(v, n),
    )
    def my_func(threshold):
        return threshold
    
    with pytest.raises(validation.ValidationError):
        my_func(threshold=-1)


def test_validate_params_decorator_skip_none():
    """Test validate_params decorator skips None values"""
    @validation.validate_params(
        threshold=lambda v, n: validation.validate_positive(v, n),
    )
    def my_func(threshold=None):
        return threshold
    
    result = my_func(threshold=None)
    assert result is None


def test_validate_params_decorator_generic_exception():
    """Test validate_params decorator catches generic exceptions from validators"""
    def broken_validator(v, n):
        raise RuntimeError("Unexpected error in validator")
        
    @validation.validate_params(
        p=broken_validator,
    )
    def my_func(p):
        return p
        
    with pytest.raises(validation.ValidationError, match="Unexpected error in validator"):
        my_func(p=10)


def test_validation_error_str():
    """Test ValidationError string representation"""
    err = validation.ValidationError("param", "test error", value=123)
    assert "param" in str(err)
    assert "test error" in str(err)
    assert "123" in str(err)


def test_validation_error_no_value():
    """Test ValidationError without value"""
    err = validation.ValidationError("param", "test error")
    assert "param" in str(err)
    assert "test error" in str(err)
