# AutomatedRelease

This repo provides a script designed to output merged PRs in the last 2 weeks for every repo with a branch 'dev' within 
the given organization. Users may also optionally filter results by team. It is meant to provide an easy way for developers
to view changes made during Agile sprint periods that are ready for release. The script outputs to a terminal the date of
the last commit for each dev branch, the title and author of the PR, the first paragraph of the PR's description, and the link
to the PR. If you do not already have a `.env` file configured for this repo, one will be created and configured for you. 

## Authentication Instructions
	
 1.  Github Token Verification
   - You need a GitHub Personal Access Token to authenticate Python with GitHub. If you don't have a token, follow the 
steps below to generate one:
     - Visit [GitHub Personal Access Tokens](https://github.com/settings/tokens) page.
     - Click on "Generate token" and provide the give access to 'read:org' and all permissions under 'repo'
     - Copy the generated token.
   - On first run, the script will prompt you to enter your token and will automatically create an `.env` file; you may also do this manually via `cp .env.example .env` and
   paste your token in yourself.

 2.   Running the program
  * Clone the repo 'AutomatedRelease' to your machine
  * Install dependencies (listed below)
    * `pip install PyGithub`
    * `pip install python-dotenv`
  * This script has one required and one optional argument. **You must provide an organization name**. You may also provide 
a team name.
  * Run the `main.py` file: `python3 main.py <org name> <optional team name>`
    * Note about Python aliases: depending on how Python is installed, you may need to run the above via `python` rather
than `python3`
  * If a `.env` file does not already exist, you will be prompted to enter your GitHub token and a `.env` file will be 
created automatically


## Dependencies
1.   [PyGithub](https://pypi.org/project/PyGithub/)
2.   [python-dotenv](https://pypi.org/project/python-dotenv/)

In order to run this program, you will need to have installed the proper dependencies.
You can do this by opening an IDE and running the command `pip install {example_module}` for each dependency you are missing.
 
 

