
echo "Stopping Live VLM WebUI server..."
pkill -f "joy_interaction_webui.server"
pkill -f "livekit-server.*livekit.yaml" || true

# Wait a moment
sleep 1

# Check if stopped
if pgrep -f "joy_interaction_webui.server" > /dev/null; then
    echo "❌ Server still running, forcing kill..."
    pkill -9 -f "joy_interaction_webui.server"
    pkill -9 -f "livekit-server.*livekit.yaml" || true
    sleep 1
fi

if ! pgrep -f "joy_interaction_webui.server" > /dev/null; then
    echo "✓ Server stopped successfully"
else
    echo "❌ Failed to stop server"
    exit 1
fi
