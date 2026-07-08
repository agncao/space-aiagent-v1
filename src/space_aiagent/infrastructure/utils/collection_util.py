def trim_list(lst, max_length):
    """
    Trim a list to a maximum length.
    """
    # 确保最大长度为非负数，使用绝对值处理可能的输入负值
    _abs_max_length = abs(max_length)
    if len(lst) > _abs_max_length:
        if max_length > 0:
            return lst[:_abs_max_length]
        else:
            return lst[-_abs_max_length:]
    return lst
