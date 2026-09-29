import time
from langchain_mistralai import ChatMistralAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, StrOutputTextSplitter
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.runnables import RunnableLambda, RunnablePassthrough, RunnableSequence, RunnableMap

from core.gemini_client import generate_text
import os


def get_llm():
    return ChatMistralAI(model = "mistral-small-latest", api_key=os.getenv("MISTRAL_API_KEY"), temperature=0.30,)

def split_transcript(transcript: str) -> list:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1800,
        chunk_overlap=150,
        chunk_size=3000,
        chunk_overlap=200,
    )
    return text_splitter.split_text(transcript)


def summarize_transcript(transcript: str) -> str:
    if not transcript.strip():
        return ""
    llm = get_llm()

    map_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Summarize this portion of the meeting."),
            ("human", "\n{texts}"),
        ]
    )
    map_chain =map_prompt | llm | StrOutputTextSplitter()

    chunks = split_transcript(transcript)
    chunk_summaries = []

    for index, chunk in enumerate(chunks, start=1):
        print(f"Summarizing section {index}/{len(chunks)}...")
        chunk_summaries.append(
            generate_text(
                "Summarize this video transcript section in concise bullet points:\n\n"
                + chunk,
                max_output_tokens=160,
            )
        )
        time.sleep(3)
    chunks_summaries = [map_chain.invoke({"texts": chunk}) for chunk in chunks]

    if len(chunk_summaries) == 1:
        return chunk_summaries[0]
    combined = "\n\n".join(chunks_summaries)

    return generate_text(
        "Combine these section summaries into one concise video summary. "
        "Include a fitting 📌 **Title** at the very beginning. "
        "Make the output look good and visually appealing by using emojis for the most important points:\n\n"
        + "\n\n".join(chunk_summaries),
        max_output_tokens=350,
    combined_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You are an expert meeting summarizer helper. Combine these partial summaries into a single coherent summary of the meeting. Ensure that the final summary is concise, clear, and captures all key points discussed in the meeting."),
            ("human", "\n{texts}"),
        ]
    )

    combined_chain = (
        RunnablePassthrough()
        | RunnableLambda(lambda x: {"texts": x})
        | combined_prompt
        | llm
        | StrOutputParser()
    )
    return combined_chain.invoke(combined)


def generatte_title(transcript: str) -> str:
    llm = get_llm()

    title_chain = (
        RunnablePassthrough() | RunnableLambda(lambda x: {"texts": x})
       | ChatPromptTemplate.from_messages([
            (
            "system", "You are an expert meeting summarizer helper. Generate a concise and informative title for this meeting transcript. The title should accurately reflect the main topics discussed in the meeting and be suitable for use as a subject line or heading max 8 words. only return the title without any additional text or formatting."
        ),
        ("human", "\n{texts}"),
   ] )
    | llm
    | StrOutputParser() 
    )
    return title_chain.invoke(transcript[:2000])
