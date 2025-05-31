#!/usr/bin/env python3
"""
Telegram Media Downloader Script

Usage:
    python telegram_downloader.py --id <ID> [--topic TOPIC_ID] (--group | --channel)
        [--concurrency N] [--skip N] [--limit N]

Arguments:
    --id         Telegram group/channel identifier (username or numeric ID).
    --group      Indicate that the ID is for a group (chat or supergroup).
    --channel    Indicate that the ID is for a channel.
    --topic      (Optional) Topic ID for forum groups to download only that topic's media.
    --concurrency N   (Optional) Maximum number of parallel downloads (default is 4).
    --skip N         (Optional) Number of initial media messages to skip (default: 0).
    --limit N        (Optional) Maximum number of media messages to download after skipping.

Examples:
    python telegram_downloader.py --id MyChannelName --channel --concurrency 8 --skip 10 --limit 50
    python telegram_downloader.py --id -1001234567890 --channel --skip 0 --limit 100
    python telegram_downloader.py --id 123456789 --group --topic 42 --concurrency 3 --skip 20 --limit 40

Note:
    - You must fill in your Telegram API credentials (api_id and api_hash) obtained from my.telegram.org.
    - If this is the first time running Telethon, you will be prompted to log in (enter the phone number and code).
"""
import os
import sys
import argparse
import asyncio
from telethon import TelegramClient, errors
from tqdm import tqdm

# >>>>>> Begin Configuration <<<<<<
# TODO: Replace these with your own Telegram API credentials
api_id = 12345678       # Your API ID (integer)
api_hash = "<API Hash>"  # Your API hash (string)
phone_number = "+<CountryCode><MobileNo>"  # Your phone number in international format
# <<<<<< End Configuration >>>>>>

def parse_args():
    parser = argparse.ArgumentParser(description="Download media from a Telegram group or channel.")
    parser.add_argument("--id", required=True, help="Telegram group/channel ID or username")
    group_or_channel = parser.add_mutually_exclusive_group(required=True)
    group_or_channel.add_argument("--group", action="store_true", help="ID is for a group (chat or supergroup)")
    group_or_channel.add_argument("--channel", action="store_true", help="ID is for a channel")
    parser.add_argument("--topic", type=int, help="Topic ID (for forum groups) to download media from that topic only")
    parser.add_argument("--concurrency", type=int, default=4, help="Max concurrent downloads (default: 4)")
    parser.add_argument("--skip", type=int, default=0, help="Number of initial media messages to skip (default: 0)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of media messages to download after skipping")
    return parser.parse_args()

def get_media_size(message):
    """Return the file size in bytes for the media in a message (if available)."""
    if message.media:
        # For documents (includes many file types)
        if getattr(message.media, "document", None) and hasattr(message.media.document, "size"):
            return message.media.document.size
        # For photos, try to pick the largest size if available
        elif getattr(message.media, "photo", None):
            sizes = message.media.photo.sizes
            if sizes:
                return max(getattr(size, 'size', 0) for size in sizes)
    return 0

async def download_media_message(client, message, output_dir, sem, index):
    """Download media from a single Telegram message with progress bar,
       prefixing the file name with its original order (index)."""
    async with sem:
        # Skip if no media
        if not message.media:
            return

        # Determine a filename
        file_name = None
        if message.document and message.document.attributes:
            for attr in message.document.attributes:
                if hasattr(attr, "file_name"):
                    file_name = attr.file_name
                    break

        # Fallback to a generic name using the message ID
        if not file_name:
            file_name = f"media_{message.id}"

        # Prepend the ordered prefix (e.g., 0011_ if original order is 11)
        prefix = f"{index:04d}_"
        file_name = prefix + file_name

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, file_name)

        # Attempt to get the total size (for progress bar)
        total_size = None
        try:
            if getattr(message.media, "document", None):
                total_size = message.media.document.size
        except Exception:
            pass

        pbar = tqdm(total=total_size, unit="B", unit_scale=True, desc=file_name, leave=True)
        prev_bytes = 0

        def progress_callback(current, total):
            nonlocal prev_bytes
            if pbar.total is None and total:
                pbar.total = total
            pbar.update(current - prev_bytes)
            prev_bytes = current

        try:
            await client.download_media(
                message,
                file=file_path,
                progress_callback=progress_callback
            )
        except Exception as e:
            pbar.clear()
            print(f"Error: Failed to download message {message.id} - {e}")
        finally:
            pbar.close()

async def main():
    args = parse_args()

    # Validate API credentials presence
    if api_id == 0 or not api_hash:
        print("Error: You must set valid API credentials in the script (api_id, api_hash).")
        sys.exit(1)

    client = TelegramClient('my_session', api_id, api_hash)
    try:
        await client.start(phone=phone_number)
    except Exception as e:
        print(f"Error: Failed to connect to Telegram API - {e}")
        await client.disconnect()
        return

    # Convert the provided id to int if possible (handles negative IDs as well)
    try:
        target = int(args.id)
    except ValueError:
        target = args.id

    try:
        entity = await client.get_entity(target)
    except errors.RPCError as e:
        print(f"Error: Could not retrieve the specified chat/channel - {e}")
        await client.disconnect()
        return
    except ValueError as e:
        print(f"Error: Invalid chat ID or username - {e}")
        await client.disconnect()
        return
    except Exception as e:
        print(f"Error: Failed to get entity for ID {args.id} - {e}")
        await client.disconnect()
        return

    # Prepare output directory
    output_dir = f"downloads_{args.id}"

    print("Fetching messages... this may take a while for large chats.")
    messages = []
    try:
        # Use reverse=True to fetch messages in ascending (oldest-first) order
        if args.topic:
            async for msg in client.iter_messages(entity, reply_to=args.topic, reverse=True):
                if msg.media:
                    messages.append(msg)
        else:
            async for msg in client.iter_messages(entity, reverse=True):
                if msg.media:
                    messages.append(msg)
    except Exception as e:
        print(f"Error: Failed to fetch messages from the chat - {e}")
        await client.disconnect()
        return

    if not messages:
        print("No media messages found in the specified chat/topic.")
        await client.disconnect()
        return

    # Apply skip and limit
    total_messages = len(messages)
    start = args.skip
    end = start + args.limit if args.limit is not None else total_messages
    messages = messages[start:end]

    if not messages:
        print("No media messages found after applying skip/limit parameters.")
        await client.disconnect()
        return

    # Calculate total download size from the selected messages
    total_bytes = sum(get_media_size(msg) for msg in messages)
    total_mb = total_bytes / (1024 * 1024)
    print(f"Total size to be downloaded: {total_bytes} bytes (~{total_mb:.2f} MB)")

    # Prompt the user to confirm before downloading
#    confirmation = input("Do you want to continue with the download? (y/n): ")
 #   if not confirmation.lower().startswith('y'):
  #      print("Download cancelled.")
   #     await client.disconnect()
    #    sys.exit(0)

    print(f"Found {total_messages} messages with media, downloading {len(messages)} messages (from {start+1} to {end}) with concurrency={args.concurrency}...")

    # Create a semaphore for concurrency
    sem = asyncio.Semaphore(args.concurrency)
    # The prefix now reflects the original order: first downloaded message gets prefix = start+1
    tasks = [
        asyncio.create_task(download_media_message(client, msg, output_dir, sem, index))
        for index, msg in enumerate(messages, start=start+1)
    ]
    await asyncio.gather(*tasks)

    print("Download completed.")
    await client.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Download interrupted by user.")
