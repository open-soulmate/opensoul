def quick_sort(arr):
    """快速排序算法"""
    if len(arr) <= 1:
        return arr
    
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    return quick_sort(left) + middle + quick_sort(right)

def quick_sort_inplace(arr, low=0, high=None):
    """原地快速排序（节省内存）"""
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        pivot_idx = partition(arr, low, high)
        quick_sort_inplace(arr, low, pivot_idx - 1)
        quick_sort_inplace(arr, pivot_idx + 1, high)
    
    return arr

def partition(arr, low, high):
    """分区函数"""
    pivot = arr[high]
    i = low - 1
    
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1

# 测试代码
if __name__ == "__main__":
    test_arr = [64, 34, 25, 12, 22, 11, 90]
    print("原数组:", test_arr)
    
    # 测试函数式快速排序
    sorted_arr = quick_sort(test_arr.copy())
    print("排序后:", sorted_arr)
    
    # 测试原地快速排序
    arr_copy = test_arr.copy()
    quick_sort_inplace(arr_copy)
    print("原地排序:", arr_copy)
    
    # 性能测试
    import time
    import random
    
    large_arr = [random.randint(1, 10000) for _ in range(10000)]
    
    start = time.time()
    quick_sort(large_arr.copy())
    print(f"函数式排序10000个元素: {time.time() - start:.4f}秒")
    
    start = time.time()
    arr_copy = large_arr.copy()
    quick_sort_inplace(arr_copy)
    print(f"原地排序10000个元素: {time.time() - start:.4f}秒")