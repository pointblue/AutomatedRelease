# AutomatedRelease


This repo is designed to output commits in the last 2 weeks for every repo with a branch 'dev'. It is meant to provide an easy way for developers to keep track of changes made during Agile sprint periods.The script outputs
to a terminal the date of the last commit for each dev branch, the title of the commit, the description associated with the commit PR, and the link to the PR.  If you do not already have a .env file configured for this repo, 
one will be
created and configured for you. 

## Authentication Instructions
	
 1.  Github Token Verification
   - You need a GitHub Personal Access Token to authenticate Python with GitHub. If you don't have a token, follow the steps below to generate one:
   - Visit [GitHub Personal Access Tokens](https://github.com/settings/tokens) page.
   - Click on "Generate token" and provide the give access to 'repos' and 'read:org'
   - Copy the generated token.
   - When prompted by the python file, enter your token 

 2.   Running the program
  * Clone the repo 'AutomatedRelease' to your machine
  * Run the 'main' file
  * If a dotenv file does not already exist, you will be prompted to enter your GitHub token
  * A env file will then be created with your token saved inside
  * If you are pushing commits to this code, remember to ensure your dotenv file is in .gitignore



## Dependencies
1.   [PyGithub](https://pypi.org/project/PyGithub/)
2.   [python-dotenv](https://pypi.org/project/python-dotenv/)

In order to run this program, you will need to have installed the proper dependencies.
You can do this by opening an IDE and running the command 'pip install {example_module}' for each dependency you are missing.

## Details

     * First, the GitHub class is created to make a connection to the GitHub API
     * The GitHub class is passed a users PA token in order to bypass rate limits
     * Using methods from the GitHub class, the program retrieves the organizations name and repos
     * Using the 'git -ls remote' command, the program retrieves the SHA from Github using the repo url
     * The repo.get_commit() method is used to get a specific commit from the SHA
 
 

