#!/bin/bash

# 默认文件后缀列表
DEFAULT_EXTENSIONS="jpg,jpeg,png,gif,bmp,webp,ico,svg,nfo,srt,ass,ssa,sub,idx,smi,ssa,SRT,sup"

# 显示帮助信息
show_help() {
    echo "用法: $0 [选项] <输入目录> <输出目录> [文件后缀名]"
    echo
    echo "选项:"
    echo "  -h, --help     显示帮助信息"
    echo "  -v, --verbose  显示详细输出"
    echo "  -e, --ext      指定文件后缀名（用逗号分隔，默认: $DEFAULT_EXTENSIONS）"
    echo
    echo "示例:"
    echo "  $0 /path/to/input /path/to/output"
    echo "  $0 -v /path/to/input /path/to/output"
    echo "  $0 -e .txt,.doc /path/to/input /path/to/output"
    exit 0
}

# 默认参数
VERBOSE=0
FILE_EXTENSIONS="$DEFAULT_EXTENSIONS"

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            ;;
        -v|--verbose)
            VERBOSE=1
            shift
            ;;
        -e|--ext)
            FILE_EXTENSIONS="$2"
            shift 2
            ;;
        *)
            # 保存非选项参数
            if [ -z "$INPUT_DIR" ]; then
                INPUT_DIR="$1"
            elif [ -z "$OUTPUT_DIR" ]; then
                OUTPUT_DIR="$1"
            else
                echo "错误: 参数过多"
                show_help
            fi
            shift
            ;;
    esac
done

# 检查必需参数
if [ -z "$INPUT_DIR" ] || [ -z "$OUTPUT_DIR" ]; then
    echo "错误: 缺少必需参数"
    show_help
fi

# 检查输入目录是否存在
if [ ! -d "$INPUT_DIR" ]; then
    echo "错误: 输入目录 '$INPUT_DIR' 不存在"
    exit 1
fi

# 创建输出目录（如果不存在）
mkdir -p "$OUTPUT_DIR"

# 显示配置信息
if [ $VERBOSE -eq 1 ]; then
    echo "配置信息:"
    echo "输入目录: $INPUT_DIR"
    echo "输出目录: $OUTPUT_DIR"
    echo "文件后缀: $FILE_EXTENSIONS"
    echo "详细模式: 开启"
    echo
fi

# 检查文件后缀函数
check_extension() {
    local file="$1"
    local ext
    IFS=',' read -ra EXTENSIONS <<< "$FILE_EXTENSIONS"
    for ext in "${EXTENSIONS[@]}"; do
        if [[ "$file" == *"$ext" ]]; then
            return 0
        fi
    done
    return 1
}

# 全量同步函数
sync_all_files() {
    if [ $VERBOSE -eq 1 ]; then
        echo "开始全量同步..."
    fi
    
    # 遍历输入目录
    find "$INPUT_DIR" -type f | while read -r file; do
        # 获取相对路径
        rel_path="${file#$INPUT_DIR/}"
        # 构建输出文件路径
        output_file="$OUTPUT_DIR/$rel_path"
        # 创建输出文件的目录
        mkdir -p "$(dirname "$output_file")"
        
        # 检查文件后缀
        if check_extension "$file"; then
            # 复制文件
            if [ $VERBOSE -eq 1 ]; then
                echo "复制文件: $file -> $output_file"
            fi
            cp -p "$file" "$output_file"
        else
            # 创建软链接
            if [ $VERBOSE -eq 1 ]; then
                echo "创建软链接: $file -> $output_file"
            fi
            ln -sf "$file" "$output_file"
        fi
    done
}

# 增量同步函数
sync_changed_file() {
    local file="$1"
    local event="$2"
    
    # 获取相对路径
    rel_path="${file#$INPUT_DIR/}"
    # 构建输出文件路径
    output_file="$OUTPUT_DIR/$rel_path"
    
    case "$event" in
        "MODIFY"|"CREATE")
            # 创建输出文件的目录
            mkdir -p "$(dirname "$output_file")"
            
            # 检查文件后缀
            if check_extension "$file"; then
                # 复制文件
                if [ $VERBOSE -eq 1 ]; then
                    echo "复制文件: $file -> $output_file"
                fi
                cp -p "$file" "$output_file"
            else
                # 创建软链接
                if [ $VERBOSE -eq 1 ]; then
                    echo "创建软链接: $file -> $output_file"
                fi
                ln -sf "$file" "$output_file"
            fi
            ;;
        "DELETE"|"MOVED_FROM")
            # 删除文件
            if [ -e "$output_file" ]; then
                if [ $VERBOSE -eq 1 ]; then
                    echo "删除文件: $output_file"
                fi
                rm -f "$output_file"
            fi
            ;;
        "MOVED_TO")
            # 处理文件移动
            if [ $VERBOSE -eq 1 ]; then
                echo "处理文件移动: $file -> $output_file"
            fi
            mkdir -p "$(dirname "$output_file")"
            if check_extension "$file"; then
                cp -p "$file" "$output_file"
            else
                ln -sf "$file" "$output_file"
            fi
            ;;
    esac
}

# 清理函数
cleanup() {
    # 删除输出目录中不存在的文件
    find "$OUTPUT_DIR" -type f | while read -r file; do
        rel_path="${file#$OUTPUT_DIR/}"
        input_file="$INPUT_DIR/$rel_path"
        if [ ! -e "$input_file" ]; then
            if [ $VERBOSE -eq 1 ]; then
                echo "删除文件: $file"
            fi
            rm -f "$file"
        fi
    done
    
    # 删除空目录
    find "$OUTPUT_DIR" -type d -empty -delete
}

# 初始全量同步
sync_all_files
cleanup

# 监控目录变化
if [ $VERBOSE -eq 1 ]; then
    echo "开始监控目录变化..."
fi

# 使用inotifywait监控目录变化
inotifywait -m -r -e modify,create,delete,move "$INPUT_DIR" | while read -r directory events filename; do
    file="$directory$filename"
    if [ $VERBOSE -eq 1 ]; then
        echo "检测到变化: $events $file"
    fi
    
    # 增量同步
    sync_changed_file "$file" "$events"
    
    # 清理
    cleanup
done 