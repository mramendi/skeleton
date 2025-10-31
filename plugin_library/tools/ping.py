"""
title: Ping
author: Misha Ramendik
version: 0.0.1
"""

import asyncio


class Tools:
    def __init__(self):
        pass

    async def ping(self, query: str) -> str:
        """
        Returns a phrase.

        :param query: A query, to which the phrase maybe responds.
        :return: The scraped and processed content as Markdown, or an error message.
        """
        return "The quick brown wok jumped over the lazy frog"


    async def pingyield(self, query: str) -> str: # type: ignore[async-generator-return]
        """
        Returns a phrase, but yields first. Uses the R2R pattern to return a value.

        :param query: A query, to which the phrase maybe responds.
        :return: The scraped and processed content as Markdown, or an error message.
        """
        yield "This is pingyield"
        raise StopAsyncIteration("The quick brown moat jumped over the lazy toad")


    async def pong(self, query: str) -> str:
        """
        Returns a phrase.

        :param query: A query, to which the phrase maybe responds.
        :return: The scraped and processed content as Markdown, or an error message.
        """
        return "The slow blue rat jumped over the lazy cat"
