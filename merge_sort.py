"""
Python 归并排序实现
"""
from typing import List


def merge_sort(arr: List[int]) -> List[int]:
    """
    归并排序算法
    时间复杂度: O(n log n)
    空间复杂度: O(n)
    """
    if len(arr) <= 1:
        return arr
    
    # 分割数组
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    
    # 合并已排序的两部分
    return merge(left, right)


def merge(left: List[int], right: List[int]) -> List[int]:
    """合并两个已排序的数组"""
    result = []
    i = j = 0
    
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    
    # 添加剩余元素
    result.extend(left[i:])
    result.extend(right[j:])
    return result


def merge_sort_inplace(arr: List[int], temp: List[int] = None, 
                       start: int = 0, end: int = None) -> None:
    """
    原地归并排序（节省空间）
    时间复杂度: O(n log n)
    空间复杂度: O(n)
    """
    if end is None:
        end = len(arr) - 1
    if temp is None:
        temp = [0] * len(arr)
    
    if start < end:
        mid = (start + end) // 2
        merge_sort_inplace(arr, temp, start, mid)
        merge_sort_inplace(arr, temp, mid + 1, end)
        merge_inplace(arr, temp, start, mid, end)


def merge_inplace(arr: List[int], temp: List[int], 
                  start: int, mid: int, end: int) -> None:
    """原地合并"""
    # 复制到临时数组
    for i in range(start, end + 1):
        temp[i] = arr[i]
    
    i, j, k = start, mid + 1, start
    while i <= mid and j <= end:
        if temp[i] <= temp[j]:
            arr[k] = temp[i]
            i += 1
        else:
            arr[k] = temp[j]
            j += 1
        k += 1
    
    while i <= mid:
        arr[k] = temp[i]
        i += 1
        k += 1
    
    while j <= end:
        arr[k] = temp[j]
        j += 1
        k += 1


# 测试代码
if __name__ == "__main__":
    import random
    
    # 测试基本版本
    test_arr = [38, 27, 43, 3, 9, 82, 10]
    print("原始数组:", test_arr)
    sorted_arr = merge_sort(test_arr)
    print("归并排序:", sorted_arr)
    
    # 测试原地版本
    test_arr2 = [38, 27, 43, 3, 9, 82, 10]
    merge_sort_inplace(test_arr2)
    print("原地排序:", test_arr2)
    
    # 随机测试
    random_arr = [random.randint(1, 100) for _ in range(10)]
    print("\n随机测试:", random_arr)
    print("排序结果:", merge_sort(random_arr))
    print("Python内置:", sorted(random_arr))
    print("结果一致:", merge_sort(random_arr) == sorted(random_arr))