#!/usr/bin/env python3
"""
快速排序算法实现模块

本模块实现经典的快速排序算法，采用分治策略对数组进行排序。
快速排序是一种高效的排序算法，平均时间复杂度为 O(n log n)。

算法原理：
1. 选择基准元素（pivot）
2. 分区（partition）：将数组分为两部分，左边元素小于基准，右边元素大于基准
3. 递归：对左右两部分分别进行快速排序

特性：
- 时间复杂度：平均 O(n log n)，最坏 O(n²)
- 空间复杂度：O(log n)
- 不稳定排序
- 原地排序

作者: SoulMate
日期: 2024
"""

from typing import List


def quicksort(arr: List[int]) -> List[int]:
    """
    快速排序主函数

    使用递归方式实现快速排序算法。
    选择数组中间元素作为基准值，避免最坏情况（已排序数组）。

    参数:
        arr (List[int]): 待排序的整数数组

    返回:
        List[int]: 排序后的新数组

    示例:
        >>> quicksort([3, 6, 8, 10, 1, 2, 1])
        [1, 1, 2, 3, 6, 8, 10]
    """
    # 基线条件：空数组或单元素数组直接返回
    if len(arr) <= 1:
        return arr

    # 选择中间元素作为基准值，避免最坏情况
    pivot = arr[len(arr) // 2]

    # 分区：创建三个列表
    # left: 小于基准值的元素
    # middle: 等于基准值的元素
    # right: 大于基准值的元素
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]

    # 递归排序左右两部分，合并结果
    # 注意：middle 已经是排序后的正确位置
    return quicksort(left) + middle + quicksort(right)


def quicksort_inplace(arr: List[int], low: int = 0, high: int = None) -> None:
    """
    原地快速排序（不创建新数组）

    通过交换元素位置实现原地排序，空间效率更高。
    使用 Lomuto 分区方案。

    参数:
        arr (List[int]): 待排序的整数数组（会被修改）
        low (int): 排序起始索引，默认为 0
        high (int): 排序结束索引，默认为数组长度-1

    返回:
        None（直接修改原数组）

    示例:
        >>> arr = [3, 6, 8, 10, 1, 2, 1]
        >>> quicksort_inplace(arr)
        >>> arr
        [1, 1, 2, 3, 6, 8, 10]
    """
    # 初始化 high 参数（避免可变默认参数问题）
    if high is None:
        high = len(arr) - 1

    # 基线条件：子数组长度大于 1 时才排序
    if low < high:
        # 执行分区操作，获取基准元素的最终位置
        pivot_index = partition(arr, low, high)

        # 递归排序基准元素左边的部分
        quicksort_inplace(arr, low, pivot_index - 1)

        # 递归排序基准元素右边的部分
        quicksort_inplace(arr, pivot_index + 1, high)


def partition(arr: List[int], low: int, high: int) -> int:
    """
    Lomuto 分区方案

    选择最后一个元素作为基准，将数组分为两部分：
    - 左边：小于等于基准的元素
    - 右边：大于基准的元素

    参数:
        arr (List[int]): 待分区的数组
        low (int): 分区起始索引
        high (int): 分区结束索引

    返回:
        int: 基准元素的最终位置索引
    """
    # 选择最后一个元素作为基准
    pivot = arr[high]

    # i 指向小于区域的末尾（初始为 low - 1）
    i = low - 1

    # 遍历数组，将小于基准的元素移到左边
    for j in range(low, high):
        if arr[j] <= pivot:
            # 扩展小于区域
            i += 1
            # 交换 arr[i] 和 arr[j]
            arr[i], arr[j] = arr[j], arr[i]

    # 将基准元素放到正确位置（小于区域的末尾）
    arr[i + 1], arr[high] = arr[high], arr[i + 1]

    # 返回基准元素的最终位置
    return i + 1


def test_quicksort():
    """
    测试函数：验证快速排序算法的正确性

    测试用例包括：
    - 常规数组
    - 已排序数组
    - 逆序数组
    - 含重复元素的数组
    - 空数组
    - 单元素数组
    """
    test_cases = [
        ([3, 6, 8, 10, 1, 2, 1], [1, 1, 2, 3, 6, 8, 10]),
        ([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]),
        ([5, 4, 3, 2, 1], [1, 2, 3, 4, 5]),
        ([], []),
        ([42], [42]),
        ([3, 3, 3, 3], [3, 3, 3, 3]),
    ]

    print("测试快速排序（创建新数组版本）：")
    for i, (input_arr, expected) in enumerate(test_cases, 1):
        result = quicksort(input_arr)
        status = "✅ 通过" if result == expected else "❌ 失败"
        print(f"  测试 {i}: {status} | 输入: {input_arr} | 输出: {result}")

    print("\n测试原地快速排序版本：")
    for i, (input_arr, expected) in enumerate(test_cases, 1):
        arr_copy = input_arr.copy()
        quicksort_inplace(arr_copy)
        status = "✅ 通过" if arr_copy == expected else "❌ 失败"
        print(f"  测试 {i}: {status} | 输入: {input_arr} | 输出: {arr_copy}")


if __name__ == "__main__":
    # 运行测试
    test_quicksort()

    # 示例用法
    print("\n" + "="*50)
    print("快速排序示例")
    print("="*50)

    data = [38, 27, 43, 3, 9, 82, 10]
    print(f"\n原始数组: {data}")

    # 使用函数式版本（创建新数组）
    sorted_data = quicksort(data)
    print(f"排序结果: {sorted_data}")

    # 使用原地排序版本
    data_copy = data.copy()
    quicksort_inplace(data_copy)
    print(f"原地排序: {data_copy}")