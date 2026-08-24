from .compiler import default_recipes

if __name__ == "__main__":
    for recipe in default_recipes():
        print(recipe.recipe_id, recipe.hash)

