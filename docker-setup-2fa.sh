#!/bin/bash

echo "Setting up Two-Factor Authentication for Instagram Video Bot..."
echo "=================================================="

# Run the 2FA setup in a temporary container
docker-compose run --rm instagram-video-bot python -m src.instagram_video_bot.setup_2fa

# Check if QR code was generated
if [ -f "2fa_qr.png" ]; then
    echo ""
    echo "✅ QR code generated successfully!"
    echo ""
    echo "Next steps:"
    echo "1. Open the file '2fa_qr.png' and scan it with Google Authenticator"
    echo "2. Copy the TOTP_SECRET value shown above"
    echo "3. Add it to your .env file"
    echo "4. Delete the QR code file for security: rm 2fa_qr.png"
    echo ""
else
    echo "❌ Failed to generate QR code. Please check the logs above."
fi 