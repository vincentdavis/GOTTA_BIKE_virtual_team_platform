"""Models for dbot_api app."""

from django.db import models


class BotStats(models.Model):
    """Records API usage: discord user, guild, endpoint, and timestamp."""

    discord_id = models.CharField(max_length=20, help_text="Discord user ID who triggered the API call")
    discord_guild_id = models.CharField(max_length=20, help_text="Discord guild/server ID")
    api = models.CharField(max_length=255, help_text="API endpoint path")
    timestamp = models.DateTimeField(auto_now_add=True, help_text="When the API call was made")

    class Meta:
        """Meta-options for BotStats."""

        verbose_name = "Bot Stats"
        verbose_name_plural = "Bot Stats"

    def __str__(self) -> str:
        """Return string representation.

        Returns:
            String with discord_id, api, and timestamp.

        """
        return f"{self.discord_id} - {self.api} @ {self.timestamp}"
