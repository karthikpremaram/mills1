from src.millis_services.millis_api import set_knowledge_base, delete_knowledge_base
from src.millis_services.millis_api import (
    get_files_in_millis,
    get_old_file_details,
    generate_presigned_url,
    upload_text_to_s3,
    create_file_in_millis,
)


async def get_old_file_fields(api_key, old_file_id):
    """Old file field (file name, file descrption, file type)"""
    try:
        response = await get_files_in_millis(api_key)
        file_details = await get_old_file_details(old_file_id, response.json())

        old_file_name = file_details.get("name", "")  # type: ignore
        old_kb_description = file_details.get("description", "")  # type: ignore
        print("old file id details fetched successfully")
        print("`file name` and `description`")
        return old_file_name, old_kb_description

    except Exception as e:
        print("Exception while fetching old file details or old file id not found", e)
        return None


async def upload_new_kb_to_millis(api_key, assistant_id, file_name, kb, kb_description):
    """ uploading new knowledge base to the agent """
    try:
        presigned_data = await generate_presigned_url(api_key, file_name)
        presigned_data = presigned_data.json()
        print("\npresigned url generated")

        s3_upload_url = presigned_data["url"]
        s3_fields = presigned_data["fields"]
        s3_key_from_fields = s3_fields["key"]
        # upload_response = await upload_text_to_s3(
        #     s3_upload_url, s3_fields, kb, file_name
        # )
        print("file uploaded to s3 bucket")

        file_size = len(kb)
        params = {
            "API_KEY": api_key,
            "assistant_id": assistant_id,
            "file_name": file_name,
            "s3_key": s3_key_from_fields,
            "kb_description": kb_description,
            "file_size": file_size,
        }
        new_file_id = await create_file_in_millis(params)
        new_file_id = new_file_id.json()
        print(f"new file created in millis with file name{file_name}")
        print("new file id:", new_file_id)

        return new_file_id

    except Exception as e:
        print("Exception while uploading new kb:", e)
        return None


async def update_kb(
    api_key, assistant_id, old_file_id, kb, file_name=None, kb_description=None
):
    """Update knowledge base (kb)"""
    try:
        # get old file fields like 'file name', 'description'
        old_file_fields = await get_old_file_fields(api_key, old_file_id)
        if old_file_fields:
            old_file_name, old_kb_description = old_file_fields
            if kb_description is None:
                kb_description = old_kb_description
            if file_name is None:
                file_name = old_file_name
        else:
            return None

        # upload new kn to millis
        new_file_id = await upload_new_kb_to_millis(
            api_key, assistant_id, file_name, kb, kb_description
        )
        if new_file_id is None:
            return None

        # set newly uloaded file as knowledge base to assistant
        response = await set_knowledge_base(api_key, assistant_id, new_file_id)

        if response.is_success:
            print(f"\nknowledgebase created")
            # delete old knowledge base
            delete_response = await delete_knowledge_base(api_key, old_file_id)
            if delete_response.is_success:
                print("old knowledgebase deleted")
            else:
                print("old knowledgebase not deleted")
        else:
            print(response.content)
            print("\nnew knowledge base not created")
            print("old knowledgebase not deleted")

        return {
            "status": "success",
            "assistant_id": assistant_id,
            "file_name": file_name,
            "new_file_id": new_file_id,
        }
    except Exception as e:
        print("Failed to create new knowledge base", e)
        return None
