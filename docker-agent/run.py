from docker_setup import (
    setup_container_and_environment,
    save_container_as_image, 
    apply_patches,
    revert_patches,
    cleanup_container
)
from test_executor import (
    run_tests_in_container,
    call_trae_agent,
    save_results_to_jsonl
)
from typing import Dict

# 示例测试规格（使用标准diff --git格式）
test_specs = [
{
    "repo": "huggingface/transformers",
    "instance_id": "huggingface__transformers-39494",
    "base_commit": "6dfd561d9cd722dfc09f702355518c6d09b9b4e3",
    "patch": [
      {
        "filename": "src/transformers/processing_utils.py",
        "status": "modified",
        "additions": 5,
        "deletions": 2,
        "changes": 7,
        "patch": "@@ -31,6 +31,8 @@\n import typing_extensions\n from huggingface_hub.errors import EntryNotFoundError\n \n+from transformers.utils import is_torch_available\n+\n from .audio_utils import load_audio\n from .dynamic_module_utils import custom_object_save\n from .feature_extraction_utils import BatchFeature\n@@ -42,6 +44,7 @@\n if is_vision_available():\n     from .image_utils import PILImageResampling\n \n+\n from .tokenization_utils_base import (\n     PaddingStrategy,\n     PreTokenizedInput,\n@@ -63,7 +66,6 @@\n     download_url,\n     is_offline_mode,\n     is_remote_url,\n-    is_torch_available,\n     list_repo_templates,\n     logging,\n )\n@@ -1559,15 +1561,16 @@ def apply_chat_template(\n \n                     for fname in video_fnames:\n                         if isinstance(fname, (list, tuple)) and isinstance(fname[0], str):\n+                            # Case a: Video is provided as a list of image file names\n                             video = [np.array(load_image(image_fname)) for image_fname in fname]\n-                            # create a 4D video because `load_video` always returns a 4D array\n                             video = np.stack(video)\n                             metadata = None\n                             logger.warning(\n                                 \"When loading the video from list of images, we cannot infer metadata such as `fps` or `duration`. \"\n                                 \"If your model requires metadata during processing, please load the whole video and let the processor sample frames instead.\"\n                             )\n                         else:\n+                            # Case b: Video is provided as a single file path or URL or decoded frames in a np.ndarray or torch.tensor\n                             video, metadata = load_video(\n                                 fname,\n                                 backend=mm_load_kwargs[\"video_load_backend\"],"
      },
      {
        "filename": "src/transformers/video_utils.py",
        "status": "modified",
        "additions": 8,
        "deletions": 2,
        "changes": 10,
        "patch": "@@ -563,6 +563,14 @@ def sample_indices_fn_func(metadata, **fn_kwargs):\n \n         sample_indices_fn = sample_indices_fn_func\n \n+    if is_valid_image(video) or (isinstance(video, (list, tuple)) and is_valid_image(video[0])):\n+        # Case 1: Video is provided as a 4D numpy array or torch tensor (frames, height, width, channels)\n+        if not is_valid_video(video):\n+            raise ValueError(\n+                f\"When passing video as decoded frames, video should be a 4D numpy array or torch tensor, but got {video.ndim} dimensions instead.\"\n+            )\n+        return video, None\n+\n     if urlparse(video).netloc in [\"www.youtube.com\", \"youtube.com\"]:\n         if not is_yt_dlp_available():\n             raise ImportError(\"To load a video from YouTube url you have  to install `yt_dlp` first.\")\n@@ -579,8 +587,6 @@ def sample_indices_fn_func(metadata, **fn_kwargs):\n         file_obj = BytesIO(requests.get(video).content)\n     elif os.path.isfile(video):\n         file_obj = video\n-    elif is_valid_image(video) or (isinstance(video, (list, tuple)) and is_valid_image(video[0])):\n-        file_obj = None\n     else:\n         raise TypeError(\"Incorrect format used for video. Should be an url linking to an video or a local path.\")\n "
      }
    ],
    "test_patch": [
      {
        "filename": "tests/models/internvl/test_processor_internvl.py",
        "status": "modified",
        "additions": 7,
        "deletions": 2,
        "changes": 9,
        "patch": "@@ -267,7 +267,7 @@ def test_apply_chat_template_video_frame_sampling(self):\n         self.assertEqual(len(out_dict_with_video[self.videos_input_name]), 2)\n \n     @require_av\n-    @parameterized.expand([(1, \"pt\"), (2, \"pt\")])\n+    @parameterized.expand([(1, \"pt\"), (2, \"pt\"), (3, \"pt\")])\n     def test_apply_chat_template_video(self, batch_size: int, return_tensors: str):\n         processor = self.get_processor()\n         if processor.chat_template is None:\n@@ -340,7 +340,12 @@ def test_apply_chat_template_video(self, batch_size: int, return_tensors: str):\n         self.assertEqual(len(out_dict[\"input_ids\"]), batch_size)\n         self.assertEqual(len(out_dict[\"attention_mask\"]), batch_size)\n \n-        video_len = 2 if batch_size == 1 else 3  # InternVL patches out and removes frames after processing\n+        # InternVL internally collects frames from all the videos in a batch and flattens the batch dimension (B T C H W) -> (B*T C H W) then patches and removes the frames\n+        # hence output length does not equal batch size\n+        # removed hardcoded video length check video_len = 2 if batch_size == 1 else 3\n+        # from experiment video_len looks like batch_size + 1\n+        # TODO: update expected video_len calculation based on the internal processing logic of InternVLProcessor\n+        video_len = batch_size + 1\n         self.assertEqual(len(out_dict[self.videos_input_name]), video_len)\n         for k in out_dict:\n             self.assertIsInstance(out_dict[k], torch.Tensor)"
      },
      {
        "filename": "tests/models/qwen2_5_omni/test_processor_qwen2_5_omni.py",
        "status": "modified",
        "additions": 8,
        "deletions": 2,
        "changes": 10,
        "patch": "@@ -422,8 +422,14 @@ def _test_apply_chat_template(\n         self.assertEqual(len(out_dict[\"input_ids\"]), batch_size)\n         self.assertEqual(len(out_dict[\"attention_mask\"]), batch_size)\n \n-        video_len = 2880 if batch_size == 1 else 5808  # qwen pixels don't scale with bs same way as other models\n-        mm_len = batch_size * 1564 if modality == \"image\" else video_len\n+        if modality == \"video\":\n+            # qwen pixels don't scale with bs same way as other models, calculate expected video token count based on video_grid_thw\n+            expected_video_token_count = 0\n+            for thw in out_dict[\"video_grid_thw\"]:\n+                expected_video_token_count += thw[0] * thw[1] * thw[2]\n+            mm_len = expected_video_token_count\n+        else:\n+            mm_len = batch_size * 1564\n         self.assertEqual(len(out_dict[input_name]), mm_len)\n \n         return_tensor_to_type = {\"pt\": torch.Tensor, \"np\": np.ndarray, None: list}"
      },
      {
        "filename": "tests/models/qwen2_5_vl/test_processor_qwen2_5_vl.py",
        "status": "modified",
        "additions": 8,
        "deletions": 2,
        "changes": 10,
        "patch": "@@ -239,8 +239,14 @@ def _test_apply_chat_template(\n         self.assertEqual(len(out_dict[\"input_ids\"]), batch_size)\n         self.assertEqual(len(out_dict[\"attention_mask\"]), batch_size)\n \n-        video_len = 180 if batch_size == 1 else 320  # qwen pixels don't scale with bs same way as other models\n-        mm_len = batch_size * 192 if modality == \"image\" else video_len\n+        if modality == \"video\":\n+            # qwen pixels don't scale with bs same way as other models, calculate expected video token count based on video_grid_thw\n+            expected_video_token_count = 0\n+            for thw in out_dict[\"video_grid_thw\"]:\n+                expected_video_token_count += thw[0] * thw[1] * thw[2]\n+            mm_len = expected_video_token_count\n+        else:\n+            mm_len = batch_size * 192\n         self.assertEqual(len(out_dict[input_name]), mm_len)\n \n         return_tensor_to_type = {\"pt\": torch.Tensor, \"np\": np.ndarray, None: list}"
      },
      {
        "filename": "tests/models/qwen2_vl/test_processor_qwen2_vl.py",
        "status": "modified",
        "additions": 8,
        "deletions": 3,
        "changes": 11,
        "patch": "@@ -239,9 +239,14 @@ def _test_apply_chat_template(\n         self.assertTrue(input_name in out_dict)\n         self.assertEqual(len(out_dict[\"input_ids\"]), batch_size)\n         self.assertEqual(len(out_dict[\"attention_mask\"]), batch_size)\n-\n-        video_len = 180 if batch_size == 1 else 320  # qwen pixels don't scale with bs same way as other models\n-        mm_len = batch_size * 192 if modality == \"image\" else video_len\n+        if modality == \"video\":\n+            # qwen pixels don't scale with bs same way as other models, calculate expected video token count based on video_grid_thw\n+            expected_video_token_count = 0\n+            for thw in out_dict[\"video_grid_thw\"]:\n+                expected_video_token_count += thw[0] * thw[1] * thw[2]\n+            mm_len = expected_video_token_count\n+        else:\n+            mm_len = batch_size * 192\n         self.assertEqual(len(out_dict[input_name]), mm_len)\n \n         return_tensor_to_type = {\"pt\": torch.Tensor, \"np\": np.ndarray, None: list}"
      },
      {
        "filename": "tests/models/smolvlm/test_processor_smolvlm.py",
        "status": "modified",
        "additions": 6,
        "deletions": 0,
        "changes": 6,
        "patch": "@@ -596,3 +596,9 @@ def test_special_mm_token_truncation(self):\n     @unittest.skip(\"SmolVLM cannot accept image URL as video frames, because it needs to know video fps and duration\")\n     def test_apply_chat_template_video_1(self):\n         pass\n+\n+    @unittest.skip(\n+        \"SmolVLM cannot accept list of decoded video frames, because it needs to know video fps and duration\"\n+    )\n+    def test_apply_chat_template_video_2(self):\n+        pass"
      },
      {
        "filename": "tests/test_processing_common.py",
        "status": "modified",
        "additions": 9,
        "deletions": 3,
        "changes": 12,
        "patch": "@@ -33,7 +33,7 @@\n     require_torch,\n     require_vision,\n )\n-from transformers.utils import is_torch_available, is_vision_available\n+from transformers.utils import is_av_available, is_torch_available, is_vision_available\n \n \n global_rng = random.Random()\n@@ -44,7 +44,6 @@\n if is_torch_available():\n     import torch\n \n-\n MODALITY_INPUT_DATA = {\n     \"images\": [\n         \"http://images.cocodataset.org/val2017/000000039769.jpg\",\n@@ -60,6 +59,13 @@\n     ],\n }\n \n+if is_av_available():\n+    from transformers.video_utils import load_video\n+\n+    # load a video file in memory for testing\n+    video, _ = load_video(\"https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_10MB.mp4\")\n+    MODALITY_INPUT_DATA[\"videos\"].append(video)\n+\n \n def prepare_image_inputs():\n     \"\"\"This function prepares a list of PIL images\"\"\"\n@@ -931,7 +937,7 @@ def test_apply_chat_template_audio(self, batch_size: int, return_tensors: str):\n         )\n \n     @require_av\n-    @parameterized.expand([(1, \"pt\"), (2, \"pt\")])  # video processor supports only torchvision\n+    @parameterized.expand([(1, \"pt\"), (2, \"pt\"), (3, \"pt\")])  # video processor supports only torchvision\n     def test_apply_chat_template_video(self, batch_size: int, return_tensors: str):\n         self._test_apply_chat_template(\n             \"video\", batch_size, return_tensors, \"videos_input_name\", \"video_processor\", MODALITY_INPUT_DATA[\"videos\"]"
      }
    ],
    "problem_statement": "I want to use live video streams directly in my chat applications without saving temporary files. Now I can pass video frames and metadata directly from my camera feed or streaming source into the chat template, making my video processing faster and more efficient.  \n\nThis update eliminates the extra step of saving videos to disk just to include them in conversations. Whether I'm building real-time video analysis tools, interactive assistants, or any application that processes live video, I can now work with frames in memory—reducing delays and simplifying my workflow.  \n\nThe improvement is especially valuable when handling continuous video streams, as I no longer need to manage temporary files or wait for disk operations. Plus, it works seamlessly with supported models like InternVL and Qwen, making video integration smoother across different platforms.  \n\nBy supporting in-memory video, this update helps me create more responsive and resource-efficient applications while keeping my code cleaner and more straightforward.",
    "hints_text": "",
    "created_at": "2025-08-01T20:02:47Z",
    "version": "v4.55.0",
    "org": "huggingface",
    "number": 39494,
    "PASS_TO_PASS": "",
    "FAIL_TO_PASS": "",
    "test_files": [
      "tests/models/internvl/test_processor_internvl.py",
      "tests/models/qwen2_5_omni/test_processor_qwen2_5_omni.py",
      "tests/models/qwen2_5_vl/test_processor_qwen2_5_vl.py",
      "tests/models/qwen2_vl/test_processor_qwen2_vl.py",
      "tests/models/smolvlm/test_processor_smolvlm.py",
      "tests/test_processing_common.py"
    ]
  }
]


def process_single_test_spec(test_spec: Dict) -> None:
    """处理单个测试规格的完整流程"""
    container = None
    try:
        print(f"\n=== 开始处理测试规格: {test_spec['instance_id']} ===")
        
        # 1. 创建容器并配置环境（带缓存支持）
        print("\n[1/6] 创建容器和环境（检查缓存）...")
        container = setup_container_and_environment(test_spec)
        
        # 2. 执行trae-agent
        print("\n[2/6] 执行trae-agent...")
        call_trae_agent(container, test_spec["instance_id"], test_spec)
        
        # # 3. 保存容器为镜像
        # print("\n[3/6] 保存容器为镜像...")
        # save_container_as_image(container, test_spec)

        # 4. 运行测试（patch后）
        print("\n[4/6] 应用测试patch...")
        test_modified_files = apply_patches(container, test_spec["test_patch"], test_spec['repo'].split('/')[-1])

        # 5. 运行测试（patch前）
        print("\n[5/6] 运行patch前的测试...")
        pre_passed, pre_logs = run_tests_in_container(container, test_spec, test_spec['repo'].split('/')[-1])
        print(f"patch前通过的测试文件: {sorted(pre_passed)}")
        
        # 6. 应用主代码patch和测试代码patch
        print("\n[6/6] 应用主patch...")
        main_modified_files = apply_patches(container, test_spec["patch"], test_spec['repo'].split('/')[-1])
        
        print("\n[6/6] 运行patch后的测试...")
        post_passed, post_logs = run_tests_in_container(container, test_spec, test_spec['repo'].split('/')[-1])
        print(f"patch后通过的测试文件: {sorted(post_passed)}")

        # 7. 撤销所有patch
        print("\n[7/7] 撤销所有patch...")
        revert_patches(container, main_modified_files, test_spec['repo'].split('/')[-1])
        revert_patches(container, test_modified_files, test_spec['repo'].split('/')[-1])

        # 8. 计算结果分类
        pass_to_pass = pre_passed & post_passed  # 前后都通过
        fail_to_pass = post_passed - pre_passed  # 仅patch后通过
        
        # 9. 记录结果
        test_spec["PASS_TO_PASS"] = ", ".join(sorted(pass_to_pass)) if pass_to_pass else "无"
        test_spec["FAIL_TO_PASS"] = ", ".join(sorted(fail_to_pass)) if fail_to_pass else "无"
        test_spec["post_passed"] = list(post_passed)
        test_spec["pre_passed"] = list(pre_passed)
        
        # 10. 输出最终结果
        print("\n=== 测试结果总结 ===")
        print(f"前后均通过的测试: {test_spec['PASS_TO_PASS']}")
        print(f"仅patch后通过的测试: {test_spec['FAIL_TO_PASS']}")
    
    except Exception as e:
        print(f"\n处理过程出错: {str(e)}")
    finally:
        # 保留容器作为缓存，不删除
        if container:
            cleanup_container(container, test_spec['instance_id'], force_remove=False)


def main():
    """主函数"""
    try:
        print(f"开始处理 {len(test_specs)} 个测试规格...")
        for test_spec in test_specs:
            process_single_test_spec(test_spec)
        save_results_to_jsonl(test_specs)
        print("\n所有测试规格处理完成！")
        
        # 可选：处理完所有测试后，询问是否清理缓存
        user_input = input("\n是否删除所有缓存容器？(y/N): ").strip().lower()
        if user_input == 'y':
            cleanup_all_cached_containers()
            
    except Exception as e:
        print(f"执行失败: {str(e)}")
        raise

def cleanup_all_cached_containers():
    """清理所有缓存的容器"""
    import docker
    client = docker.from_env()
    
    for test_spec in test_specs:
        instance_id = test_spec["instance_id"]
        try:
            container = client.containers.get(instance_id)
            container.stop()
            container.remove()
            print(f"已删除缓存容器: {instance_id}")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            print(f"删除容器 {instance_id} 时出错: {str(e)}")

if __name__ == "__main__":
    main()